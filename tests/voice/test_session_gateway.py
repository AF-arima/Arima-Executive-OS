import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Role
from app.orchestration.exceptions import OrchestrationApprovalRequired
from app.orchestration.factory import OrchestrationFactory
from app.voice.exceptions import VoicePermissionDenied
from app.services.exceptions import ResourceNotFoundError
from app.voice.gateway import (
    MockSpeechToTextProvider,
    MockTextToSpeechProvider,
    VoiceGateway,
)
from app.voice.schemas import VoiceSessionCreate
from app.voice.session import VoiceSessionStore
from app.voice.state import VoiceState
from tests.database.helpers import sqlite_session
from tests.orchestration.helpers import make_context

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)


class CountingEngine:
    def __init__(self, engine) -> None:
        self.engine = engine
        self.calls = 0

    async def execute(self, context):
        self.calls += 1
        return await self.engine.execute(context)

    async def health(self):
        return await self.engine.health()


class ApprovalEngine:
    async def execute(self, context):
        del context
        raise OrchestrationApprovalRequired

    async def health(self):
        return []


async def build_gateway(
    database: AsyncSession,
    *,
    approval: bool = False,
    context_error: bool = False,
):
    context = await make_context(database)

    async def context_factory(voice_session, actor, transcript):
        del voice_session, actor
        if context_error:
            raise ResourceNotFoundError("Default agent not found")
        return context.__class__(
            user=context.user,
            agent=context.agent,
            conversation=context.conversation,
            run=context.run,
            request=context.request.model_copy(
                update={"content": transcript, "stream": True}
            ),
            permissions=context.permissions,
            correlation_id=context.correlation_id,
            current_timestamp=context.current_timestamp,
        )

    engine = (
        ApprovalEngine()
        if approval
        else CountingEngine(OrchestrationFactory(database).create())
    )
    store = VoiceSessionStore(database, clock=lambda: NOW)
    gateway = VoiceGateway(
        sessions=store,
        orchestration=engine,
        context_factory=context_factory,
    )
    return gateway, context.user, engine


def test_session_creation_and_navigation_command() -> None:
    async def scenario() -> None:
        async with sqlite_session() as database:
            gateway, actor, engine = await build_gateway(database)
            session, events = await gateway.create_session(
                VoiceSessionCreate(), actor
            )
            assert session.state is VoiceState.IDLE
            assert events[0].event.value == "session_started"
            response = await gateway.handle_transcript(
                session.session_id, "Open my portfolio", actor
            )
            assert response.navigation_action.path == "/portfolio-lab"
            assert response.state is VoiceState.COMPLETED
            assert engine.calls == 0
            assert any(
                event.type.value == "chamber_transition_requested"
                and event.target_chamber.value == "portfolio"
                for event in response.experience_events
            )

    asyncio.run(scenario())


def test_unknown_request_delegates_to_orchestration() -> None:
    async def scenario() -> None:
        async with sqlite_session() as database:
            gateway, actor, engine = await build_gateway(database)
            session, _ = await gateway.create_session(
                VoiceSessionCreate(), actor
            )
            response = await gateway.handle_transcript(
                session.session_id,
                "Analyse this strategic decision",
                actor,
            )
            assert engine.calls == 1
            assert response.response_text.startswith("Mock response:")
            assert any(
                event.event.value == "thinking_started"
                for event in response.events
            )
            assert any(
                event.type.value == "data_object_created"
                for event in response.experience_events
            )

    asyncio.run(scenario())


def test_panel_interrupt_cancel_and_repeat() -> None:
    async def scenario() -> None:
        async with sqlite_session() as database:
            gateway, actor, _ = await build_gateway(database)
            session, _ = await gateway.create_session(
                VoiceSessionCreate(), actor
            )
            briefing = await gateway.handle_transcript(
                session.session_id, "Show today's briefing", actor
            )
            assert briefing.panel_action.focus == "today"
            repeated = await gateway.handle_transcript(
                session.session_id, "Say that again", actor
            )
            assert repeated.response_text == briefing.response_text
            interrupted = await gateway.interrupt(session.session_id, actor)
            assert interrupted.state is VoiceState.INTERRUPTED
            cancelled = await gateway.cancel(session.session_id, actor)
            assert cancelled.state is VoiceState.CANCELLED

    asyncio.run(scenario())


def test_growth_requires_authorised_role() -> None:
    async def scenario() -> None:
        async with sqlite_session() as database:
            gateway, actor, _ = await build_gateway(database)
            actor.roles = [Role(name="analyst", description=None)]
            session, _ = await gateway.create_session(
                VoiceSessionCreate(), actor
            )
            with pytest.raises(VoicePermissionDenied):
                await gateway.handle_transcript(
                    session.session_id,
                    "Show me what Growth created today",
                    actor,
                )

    asyncio.run(scenario())


def test_approval_response_and_health() -> None:
    async def scenario() -> None:
        async with sqlite_session() as database:
            gateway, actor, _ = await build_gateway(
                database, approval=True
            )
            session, _ = await gateway.create_session(
                VoiceSessionCreate(), actor
            )
            response = await gateway.handle_transcript(
                session.session_id, "Execute sensitive operation", actor
            )
            assert response.state is VoiceState.AWAITING_APPROVAL
            assert response.approval_request is not None
            assert response.events[-1].event.value == "approval_required"
            health = await gateway.health()
            assert health.status == "healthy"

    asyncio.run(scenario())


def test_mock_browser_provider_contracts_are_deterministic() -> None:
    async def scenario() -> None:
        stt = await MockSpeechToTextProvider().transcribe(
            b"ignored", language="en"
        )
        tts = await MockTextToSpeechProvider().synthesize(
            "Arima", language="en"
        )
        assert stt == "Deterministic mock transcript"
        assert tts == b"Arima"

    asyncio.run(scenario())


def test_context_failure_marks_durable_session_recoverable() -> None:
    async def scenario() -> None:
        async with sqlite_session() as database:
            gateway, actor, _ = await build_gateway(
                database, context_error=True
            )
            session, _ = await gateway.create_session(
                VoiceSessionCreate(), actor
            )

            with pytest.raises(
                ResourceNotFoundError, match="Default agent not found"
            ):
                await gateway.handle_transcript(
                    session.session_id, "Hello", actor
                )

            failed = await gateway.sessions.get(session.session_id, actor.id)
            assert failed.state is VoiceState.ERROR

    asyncio.run(scenario())
