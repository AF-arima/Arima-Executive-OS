from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.database.models import User
from app.experience.mapper import ExperienceEventMapper
from app.orchestration.context import OrchestrationExecutionContext
from app.orchestration.engine import OrchestrationEngine
from app.orchestration.exceptions import OrchestrationApprovalRequired
from app.orchestration.schemas import OrchestrationResult
from app.services.permissions import user_roles
from app.voice.commands import (
    VoiceCommandName,
    extract_command,
    resolve_command,
)
from app.voice.events import VoiceEventType
from app.voice.exceptions import VoicePermissionDenied
from app.voice.health import voice_health
from app.voice.schemas import (
    VoiceApprovalAction,
    VoiceCommand,
    VoiceEvent,
    VoiceGatewayResponse,
    VoiceNavigationAction,
    VoicePanelAction,
    VoiceSession,
    VoiceSessionCreate,
)
from app.voice.session import VoiceSessionStore
from app.voice.state import VoiceState


class SpeechToTextProvider(ABC):
    """Future speech input contract; browser speech owns the MVP path."""

    @abstractmethod
    async def transcribe(self, audio: bytes, *, language: str) -> str:
        raise NotImplementedError


class TextToSpeechProvider(ABC):
    """Future speech output contract; browser synthesis owns the MVP path."""

    @abstractmethod
    async def synthesize(self, text: str, *, language: str) -> bytes:
        raise NotImplementedError


class MockSpeechToTextProvider(SpeechToTextProvider):
    async def transcribe(self, audio: bytes, *, language: str) -> str:
        del audio, language
        return "Deterministic mock transcript"


class MockTextToSpeechProvider(TextToSpeechProvider):
    async def synthesize(self, text: str, *, language: str) -> bytes:
        del language
        return text.encode()


class ContextFactory(Protocol):
    async def __call__(
        self,
        voice_session: VoiceSession,
        actor: User,
        transcript: str,
    ) -> OrchestrationExecutionContext: ...


HealthCheck = Callable[[], Awaitable[object]]
GROWTH_ROLES = frozenset({"administrator", "executive", "manager"})


class VoiceGateway:
    def __init__(
        self,
        *,
        sessions: VoiceSessionStore,
        orchestration: OrchestrationEngine,
        context_factory: ContextFactory,
        enabled: bool = True,
        experience_mapper: ExperienceEventMapper | None = None,
    ) -> None:
        self.sessions = sessions
        self.orchestration = orchestration
        self.context_factory = context_factory
        self.enabled = enabled
        self.experience_mapper = experience_mapper or ExperienceEventMapper()

    async def create_session(
        self,
        data: VoiceSessionCreate,
        actor: User,
    ) -> tuple[VoiceSession, list[VoiceEvent]]:
        session = await self.sessions.create(data, actor.id)
        return session, [
            self._event(
                VoiceEventType.SESSION_STARTED,
                0,
                session.created_at,
                {"state": session.state.value},
            )
        ]

    async def handle_transcript(
        self,
        session_id: UUID,
        transcript: str,
        actor: User,
    ) -> VoiceGatewayResponse:
        session = await self.sessions.get(session_id, actor.id)
        previous_response = session.response_text
        session = await self.sessions.update(
            session_id,
            actor.id,
            state=VoiceState.PROCESSING,
            transcript=transcript,
        )
        events = [
            self._event(
                VoiceEventType.TRANSCRIPT_FINAL,
                0,
                session.updated_at,
                {"transcript": transcript},
            )
        ]
        command = extract_command(transcript)
        if command is not None:
            return await self._handle_command(
                session,
                command,
                actor,
                previous_response,
                events,
            )
        return await self._orchestrate(session, transcript, actor, events)

    async def interrupt(
        self, session_id: UUID, actor: User
    ) -> VoiceGatewayResponse:
        session = await self.sessions.get(session_id, actor.id)
        session = await self.sessions.update(
            session_id, actor.id, state=VoiceState.INTERRUPTED
        )
        events = [
            self._event(
                VoiceEventType.SPEAKING_STOPPED,
                0,
                session.updated_at,
                {"reason": "interrupted"},
            )
        ]
        return self._response(
            session,
            session.response_text or "Speech interrupted.",
            events,
        )

    async def cancel(
        self, session_id: UUID, actor: User
    ) -> VoiceGatewayResponse:
        session = await self.sessions.get(session_id, actor.id)
        if session.state is VoiceState.CANCELLED:
            return self._response(session, "Voice session cancelled.", [])
        session = await self.sessions.update(
            session_id, actor.id, state=VoiceState.CANCELLED
        )
        events = [
            self._event(
                VoiceEventType.SPEAKING_STOPPED,
                0,
                session.updated_at,
                {"reason": "cancelled"},
            ),
            self._event(
                VoiceEventType.SESSION_COMPLETED,
                1,
                session.updated_at,
                {"state": VoiceState.CANCELLED.value},
            ),
        ]
        return self._response(session, "Voice session cancelled.", events)

    async def health(self):
        available = False
        try:
            await self.orchestration.health()
            available = True
        except Exception:
            available = False
        return voice_health(
            enabled=self.enabled,
            orchestration_available=available,
        )

    async def _handle_command(
        self,
        session: VoiceSession,
        command: VoiceCommand,
        actor: User,
        previous_response: str | None,
        events: list[VoiceEvent],
    ) -> VoiceGatewayResponse:
        name = VoiceCommandName(command.name)
        if name in {
            VoiceCommandName.OPEN_GROWTH_STUDIO,
            VoiceCommandName.GROWTH_TODAY,
        } and GROWTH_ROLES.isdisjoint(user_roles(actor)):
            raise VoicePermissionDenied(
                "Growth Studio requires administrator, executive, "
                "or manager access"
            )
        action = resolve_command(command)
        if name is VoiceCommandName.CANCEL:
            return await self.cancel(session.session_id, actor)
        if name is VoiceCommandName.STOP_SPEAKING:
            session = await self.sessions.update(
                session.session_id,
                actor.id,
                state=VoiceState.SPEAKING,
            )
            return await self.interrupt(session.session_id, actor)
        response = (
            previous_response
            if name is VoiceCommandName.REPEAT and previous_response
            else action.response
        )
        session = await self.sessions.update(
            session.session_id,
            actor.id,
            state=VoiceState.SPEAKING,
            response_text=response,
        )
        self._append_action_events(
            events,
            session.updated_at,
            action.navigation,
            action.panel,
        )
        events.append(
            self._event(
                VoiceEventType.SPEAKING_STARTED,
                len(events),
                session.updated_at,
                {"text": response},
            )
        )
        session = await self.sessions.update(
            session.session_id,
            actor.id,
            state=VoiceState.COMPLETED,
        )
        events.append(
            self._event(
                VoiceEventType.SESSION_COMPLETED,
                len(events),
                session.updated_at,
            )
        )
        return self._response(
            session,
            response,
            events,
            navigation=action.navigation,
            panel=action.panel,
        )

    async def _orchestrate(
        self,
        session: VoiceSession,
        transcript: str,
        actor: User,
        events: list[VoiceEvent],
    ) -> VoiceGatewayResponse:
        session = await self.sessions.update(
            session.session_id,
            actor.id,
            state=VoiceState.THINKING,
        )
        events.append(
            self._event(
                VoiceEventType.THINKING_STARTED,
                len(events),
                session.updated_at,
            )
        )
        context = await self.context_factory(session, actor, transcript)
        session = await self.sessions.update(
            session.session_id,
            actor.id,
            conversation_id=context.conversation.id,
            run_id=context.run.id,
            correlation_id=context.correlation_id,
        )
        try:
            result = await self.orchestration.execute(context)
        except OrchestrationApprovalRequired:
            session = await self.sessions.update(
                session.session_id,
                actor.id,
                state=VoiceState.AWAITING_APPROVAL,
            )
            approval = VoiceApprovalAction(
                title="Approval required",
                reason=(
                    "The requested action contains a protected operation."
                ),
            )
            events.append(
                self._event(
                    VoiceEventType.APPROVAL_REQUIRED,
                    len(events),
                    session.updated_at,
                    approval.model_dump(mode="json"),
                )
            )
            return self._response(
                session,
                "I need your approval before I can continue.",
                events,
                approval=approval,
            )
        actions = (
            result.executed_tools
            + result.executed_integrations
            + result.executed_jobs
        )
        if actions:
            session = await self.sessions.update(
                session.session_id,
                actor.id,
                state=VoiceState.TOOL_EXECUTION,
            )
            for action in actions:
                events.extend(
                    [
                        self._event(
                            VoiceEventType.TOOL_STARTED,
                            len(events),
                            session.updated_at,
                            {"tool": action.name},
                        ),
                        self._event(
                            VoiceEventType.TOOL_COMPLETED,
                            len(events) + 1,
                            session.updated_at,
                            {
                                "tool": action.name,
                                "success": action.success,
                            },
                        ),
                    ]
                )
        response = result.final_response
        for chunk in result.chunks:
            if chunk.content:
                events.append(
                    self._event(
                        VoiceEventType.RESPONSE_CHUNK,
                        len(events),
                        session.updated_at,
                        {"text": chunk.content},
                    )
                )
        if not result.chunks:
            events.append(
                self._event(
                    VoiceEventType.RESPONSE_CHUNK,
                    len(events),
                    session.updated_at,
                    {"text": response},
                )
            )
        session = await self.sessions.update(
            session.session_id,
            actor.id,
            state=VoiceState.SPEAKING,
            response_text=response,
        )
        events.append(
            self._event(
                VoiceEventType.SPEAKING_STARTED,
                len(events),
                session.updated_at,
                {"text": response},
            )
        )
        session = await self.sessions.update(
            session.session_id,
            actor.id,
            state=VoiceState.COMPLETED,
        )
        events.append(
            self._event(
                VoiceEventType.SESSION_COMPLETED,
                len(events),
                session.updated_at,
            )
        )
        return self._response(
            session,
            response,
            events,
            orchestration_result=result,
        )

    @staticmethod
    def _event(
        event: VoiceEventType,
        sequence: int,
        timestamp: datetime,
        data: dict[str, object] | None = None,
    ) -> VoiceEvent:
        return VoiceEvent(
            event=event,
            sequence=sequence,
            timestamp=timestamp,
            data=data or {},
        )

    def _append_action_events(
        self,
        events: list[VoiceEvent],
        timestamp: datetime,
        navigation: VoiceNavigationAction | None,
        panel: VoicePanelAction | None,
    ) -> None:
        if navigation is not None:
            events.append(
                self._event(
                    VoiceEventType.NAVIGATION_REQUESTED,
                    len(events),
                    timestamp,
                    navigation.model_dump(),
                )
            )
        if panel is not None:
            events.append(
                self._event(
                    VoiceEventType.PANEL_REQUESTED,
                    len(events),
                    timestamp,
                    panel.model_dump(),
                )
            )

    def _response(
        self,
        session: VoiceSession,
        text: str,
        events: list[VoiceEvent],
        *,
        navigation: VoiceNavigationAction | None = None,
        panel: VoicePanelAction | None = None,
        approval: VoiceApprovalAction | None = None,
        orchestration_result: OrchestrationResult | None = None,
    ) -> VoiceGatewayResponse:
        return VoiceGatewayResponse(
            session_id=session.session_id,
            correlation_id=session.correlation_id,
            state=session.state,
            transcript=session.transcript,
            response_text=text,
            visual_response_text=text,
            navigation_action=navigation,
            panel_action=panel,
            approval_request=approval,
            events=events,
            experience_events=self.experience_mapper.from_gateway_events(
                session=session,
                voice_events=events,
                orchestration_result=orchestration_result,
            ),
        )
