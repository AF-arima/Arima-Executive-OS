"""Map existing backend outcomes into client-side experience events.

This adapter is deliberately side-effect free: permissions, execution,
telemetry, notifications, and audit records remain owned by their existing
platform services.
"""

from __future__ import annotations

from typing import Any

from app.experience.schemas import (
    ExperienceChamber,
    ExperienceEvent,
    ExperienceEventPriority,
    ExperienceEventType,
)
from app.orchestration.schemas import (
    ExecutedAction,
    OrchestrationIntent,
    OrchestrationResult,
)
from app.voice.events import VoiceEventType
from app.voice.schemas import VoiceEvent, VoiceSession


class ExperienceEventMapper:
    """Create visual-only events from voice and orchestration contracts."""

    def from_gateway_events(
        self,
        *,
        session: VoiceSession,
        voice_events: list[VoiceEvent],
        orchestration_result: OrchestrationResult | None = None,
    ) -> list[ExperienceEvent]:
        events = [
            experience_event
            for voice_event in voice_events
            for experience_event in self.from_voice_event(session, voice_event)
        ]
        if orchestration_result is not None:
            events.extend(
                self.from_orchestration_result(session, orchestration_result)
            )
        return events

    def from_voice_event(
        self,
        session: VoiceSession,
        voice_event: VoiceEvent,
    ) -> list[ExperienceEvent]:
        event = voice_event.event
        timestamp = voice_event.timestamp
        data = voice_event.data

        if event is VoiceEventType.SESSION_STARTED:
            return [
                self._event(
                    session=session,
                    timestamp=timestamp,
                    type=ExperienceEventType.AVATAR_STATE_CHANGED,
                    source="voice_gateway",
                    payload={"state": "awakening"},
                    duration_hint=700,
                    dismissible=False,
                ),
                self._event(
                    session=session,
                    timestamp=timestamp,
                    type=ExperienceEventType.SYSTEM_PULSE,
                    source="voice_gateway",
                    target_chamber=ExperienceChamber.EXECUTIVE,
                    payload={"state": data.get("state", "idle")},
                    duration_hint=1_000,
                    dismissible=False,
                ),
            ]
        if event is VoiceEventType.MICROPHONE_READY:
            return [
                self._avatar_event(session, timestamp, "idle")
            ]
        if event is VoiceEventType.LISTENING_STARTED:
            return [
                self._avatar_event(session, timestamp, "listening"),
                self._event(
                    session=session,
                    timestamp=timestamp,
                    type=ExperienceEventType.NEURAL_ACTIVITY_STARTED,
                    source="voice_gateway",
                    payload={"activity": "listening"},
                    duration_hint=2_000,
                    dismissible=False,
                ),
            ]
        if event in {
            VoiceEventType.TRANSCRIPT_PARTIAL,
            VoiceEventType.TRANSCRIPT_FINAL,
        }:
            return [
                self._avatar_event(
                    session,
                    timestamp,
                    "speech_detected",
                    payload={"transcript": data.get("transcript", "")},
                )
            ]
        if event is VoiceEventType.THINKING_STARTED:
            return [
                self._avatar_event(session, timestamp, "thinking"),
                self._event(
                    session=session,
                    timestamp=timestamp,
                    type=ExperienceEventType.NEURAL_ACTIVITY_STARTED,
                    source="voice_gateway",
                    payload={"activity": "thinking"},
                    duration_hint=2_000,
                    dismissible=False,
                ),
            ]
        if event is VoiceEventType.TOOL_STARTED:
            tool_name = str(data.get("tool", "operation"))
            chamber = self.chamber_for_name(tool_name)
            return [
                self._avatar_event(session, timestamp, "executing"),
                self._event(
                    session=session,
                    timestamp=timestamp,
                    type=ExperienceEventType.NEURAL_ACTIVITY_STARTED,
                    source="tool",
                    target_chamber=chamber,
                    payload={"activity": "tool", "tool": tool_name},
                    duration_hint=1_500,
                    dismissible=False,
                ),
            ]
        if event is VoiceEventType.TOOL_COMPLETED:
            tool_name = str(data.get("tool", "operation"))
            success = bool(data.get("success", True))
            chamber = self.chamber_for_name(tool_name)
            completed = self._event(
                session=session,
                timestamp=timestamp,
                type=ExperienceEventType.NEURAL_ACTIVITY_COMPLETED,
                source="tool",
                target_chamber=chamber,
                priority=(
                    ExperienceEventPriority.NORMAL
                    if success
                    else ExperienceEventPriority.HIGH
                ),
                payload={
                    "activity": "tool",
                    "tool": tool_name,
                    "success": success,
                },
                duration_hint=900,
                dismissible=False,
            )
            if not success:
                return [
                    completed,
                    self._warning_event(
                        session,
                        timestamp,
                        source="tool",
                        message=f"{tool_name} did not complete successfully.",
                        target_chamber=chamber,
                    ),
                ]
            return [
                completed,
                self._event(
                    session=session,
                    timestamp=timestamp,
                    type=ExperienceEventType.TASK_VISUALISATION_REQUESTED,
                    source="tool",
                    target_chamber=chamber,
                    payload={
                        "task": tool_name,
                        "status": "completed",
                    },
                    duration_hint=2_500,
                ),
            ]
        if event is VoiceEventType.APPROVAL_REQUIRED:
            return [
                self._avatar_event(
                    session,
                    timestamp,
                    "awaiting_approval",
                ),
                self._event(
                    session=session,
                    timestamp=timestamp,
                    type=(
                        ExperienceEventType.APPROVAL_VISUALISATION_REQUESTED
                    ),
                    source="approval",
                    target_chamber=ExperienceChamber.APPROVALS,
                    priority=ExperienceEventPriority.HIGH,
                    payload=data,
                    duration_hint=8_000,
                    requires_attention=True,
                ),
            ]
        if event is VoiceEventType.RESPONSE_CHUNK:
            return [
                self._event(
                    session=session,
                    timestamp=timestamp,
                    type=ExperienceEventType.DATA_OBJECT_UPDATED,
                    source="voice_gateway",
                    target_chamber=ExperienceChamber.EXECUTIVE,
                    payload={
                        "object": "voice_response",
                        "content": data.get("text", ""),
                    },
                    dismissible=False,
                )
            ]
        if event is VoiceEventType.NAVIGATION_REQUESTED:
            path = str(data.get("path", ""))
            chamber = self.chamber_for_path(path)
            return [
                self._event(
                    session=session,
                    timestamp=timestamp,
                    type=(
                        ExperienceEventType.CHAMBER_TRANSITION_REQUESTED
                    ),
                    source="voice_command",
                    target_chamber=chamber,
                    payload={
                        "path": path,
                        "label": data.get("label", ""),
                        "focus": data.get("focus"),
                        "direction": "back" if path == "back" else "forward",
                    },
                    duration_hint=1_400,
                    dismissible=False,
                )
            ]
        if event is VoiceEventType.PANEL_REQUESTED:
            return self._panel_events(session, timestamp, data)
        if event is VoiceEventType.SPEAKING_STARTED:
            return [
                self._avatar_event(
                    session,
                    timestamp,
                    "speaking",
                    payload={"text": data.get("text", "")},
                )
            ]
        if event is VoiceEventType.SPEAKING_STOPPED:
            state = (
                "interrupted"
                if data.get("reason") == "interrupted"
                else "idle"
            )
            return [self._avatar_event(session, timestamp, state)]
        if event is VoiceEventType.SESSION_COMPLETED:
            return [
                self._avatar_event(session, timestamp, "completed"),
                self._event(
                    session=session,
                    timestamp=timestamp,
                    type=ExperienceEventType.NEURAL_ACTIVITY_COMPLETED,
                    source="voice_gateway",
                    payload={"activity": "response"},
                    duration_hint=800,
                    dismissible=False,
                ),
            ]
        if event is VoiceEventType.SESSION_FAILED:
            return [
                self._avatar_event(session, timestamp, "error"),
                self._warning_event(
                    session,
                    timestamp,
                    source="voice_gateway",
                    message=str(data.get("message", "Voice session failed.")),
                ),
            ]
        return []

    def from_orchestration_result(
        self,
        session: VoiceSession,
        result: OrchestrationResult,
    ) -> list[ExperienceEvent]:
        timestamp = result.plan.created_at
        chamber = self.chamber_for_intent(result.intent)
        demo = result.route.provider == "mock"
        events = [
            self._event(
                session=session,
                timestamp=timestamp,
                type=ExperienceEventType.DATA_OBJECT_CREATED,
                source="orchestration",
                target_chamber=chamber,
                payload={
                    "object": "orchestration_result",
                    "intent": result.intent.value,
                    "response": result.final_response,
                    "demo": demo,
                },
                duration_hint=2_000,
            ),
            self._event(
                session=session,
                timestamp=timestamp,
                type=ExperienceEventType.SYSTEM_PULSE,
                source="telemetry",
                target_chamber=ExperienceChamber.HEALTH,
                payload={
                    "provider": result.route.provider,
                    "model": result.route.model,
                    "latency_ms": result.latency_ms,
                    "success": not result.failures,
                    "demo": demo,
                },
                duration_hint=1_000,
                dismissible=False,
            ),
        ]
        for action in result.executed_tools + result.executed_integrations:
            events.extend(self._action_events(session, timestamp, action))
        for action in result.executed_jobs:
            events.extend(self._action_events(session, timestamp, action))
            events.append(
                self._event(
                    session=session,
                    timestamp=timestamp,
                    type=ExperienceEventType.BACKGROUND_JOB_COMPLETED,
                    source="background",
                    target_chamber=self.chamber_for_name(action.name),
                    priority=(
                        ExperienceEventPriority.NORMAL
                        if action.success
                        else ExperienceEventPriority.HIGH
                    ),
                    payload=self._action_payload(action),
                    duration_hint=2_000,
                    requires_attention=not action.success,
                )
            )
        if result.intent is OrchestrationIntent.PORTFOLIO:
            events.append(
                self._event(
                    session=session,
                    timestamp=timestamp,
                    type=(
                        ExperienceEventType.PERFORMANCE_VISUALISATION_REQUESTED
                    ),
                    source="orchestration",
                    target_chamber=ExperienceChamber.PORTFOLIO,
                    payload={"demo": demo, "intent": result.intent.value},
                    duration_hint=3_500,
                )
            )
        if result.intent is OrchestrationIntent.QUANT:
            events.append(
                self._event(
                    session=session,
                    timestamp=timestamp,
                    type=ExperienceEventType.DATA_OBJECT_CREATED,
                    source="orchestration",
                    target_chamber=ExperienceChamber.QUANT,
                    payload={
                        "object": "research_result",
                        "demo": demo,
                    },
                    duration_hint=2_500,
                )
            )
        for approval in result.approvals:
            if not approval.approved:
                events.append(
                    self._event(
                        session=session,
                        timestamp=timestamp,
                        type=(
                            ExperienceEventType
                            .APPROVAL_VISUALISATION_REQUESTED
                        ),
                        source="orchestration",
                        target_chamber=ExperienceChamber.APPROVALS,
                        priority=ExperienceEventPriority.HIGH,
                        payload=approval.model_dump(mode="json"),
                        duration_hint=8_000,
                        requires_attention=True,
                    )
                )
        for warning in result.warnings:
            events.append(
                self._warning_event(
                    session,
                    timestamp,
                    source="orchestration",
                    message=warning,
                    target_chamber=chamber,
                )
            )
        for failure in result.failures:
            events.append(
                self._warning_event(
                    session,
                    timestamp,
                    source="orchestration",
                    message=failure,
                    target_chamber=chamber,
                )
            )
        return events

    def _action_events(
        self,
        session: VoiceSession,
        timestamp: Any,
        action: ExecutedAction,
    ) -> list[ExperienceEvent]:
        chamber = self.chamber_for_name(action.name)
        payload = self._action_payload(action)
        events = [
            self._event(
                session=session,
                timestamp=timestamp,
                type=ExperienceEventType.NEURAL_ACTIVITY_COMPLETED,
                source="orchestration",
                target_chamber=chamber,
                priority=(
                    ExperienceEventPriority.NORMAL
                    if action.success
                    else ExperienceEventPriority.HIGH
                ),
                payload=payload,
                duration_hint=1_000,
                dismissible=False,
            )
        ]
        if action.success and chamber in {
            ExperienceChamber.PROJECTS,
            ExperienceChamber.GROWTH,
        }:
            events.append(
                self._event(
                    session=session,
                    timestamp=timestamp,
                    type=ExperienceEventType.TASK_VISUALISATION_REQUESTED,
                    source="orchestration",
                    target_chamber=chamber,
                    payload=payload,
                    duration_hint=2_500,
                )
            )
        if action.success and chamber is ExperienceChamber.PORTFOLIO:
            events.append(
                self._event(
                    session=session,
                    timestamp=timestamp,
                    type=(
                        ExperienceEventType.PERFORMANCE_VISUALISATION_REQUESTED
                    ),
                    source="orchestration",
                    target_chamber=chamber,
                    payload=payload,
                    duration_hint=3_500,
                )
            )
        if not action.success:
            events.append(
                self._warning_event(
                    session,
                    timestamp,
                    source="orchestration",
                    message=action.error or f"{action.name} failed.",
                    target_chamber=chamber,
                )
            )
        return events

    @staticmethod
    def chamber_for_path(path: str) -> ExperienceChamber | None:
        normalized = path.lower()
        if "portfolio" in normalized:
            return ExperienceChamber.PORTFOLIO
        if "quant" in normalized:
            return ExperienceChamber.QUANT
        if "growth" in normalized:
            return ExperienceChamber.GROWTH
        if "project" in normalized:
            return ExperienceChamber.PROJECTS
        if "approval" in normalized:
            return ExperienceChamber.APPROVALS
        if "health" in normalized:
            return ExperienceChamber.HEALTH
        if "executive" in normalized:
            return ExperienceChamber.EXECUTIVE
        return None

    @classmethod
    def chamber_for_name(cls, name: str) -> ExperienceChamber | None:
        return cls.chamber_for_path(name)

    @staticmethod
    def chamber_for_intent(
        intent: OrchestrationIntent,
    ) -> ExperienceChamber:
        mapping = {
            OrchestrationIntent.PORTFOLIO: ExperienceChamber.PORTFOLIO,
            OrchestrationIntent.QUANT: ExperienceChamber.QUANT,
            OrchestrationIntent.GROWTH: ExperienceChamber.GROWTH,
            OrchestrationIntent.PROJECTS: ExperienceChamber.PROJECTS,
            OrchestrationIntent.TASK: ExperienceChamber.PROJECTS,
        }
        return mapping.get(intent, ExperienceChamber.EXECUTIVE)

    def _panel_events(
        self,
        session: VoiceSession,
        timestamp: Any,
        data: dict[str, Any],
    ) -> list[ExperienceEvent]:
        panel = str(data.get("panel", ""))
        focus = data.get("focus")
        if panel == "executive_briefing" and focus == "today":
            payload = {
                "object": "daily_intelligence",
                "presentation": "daily",
                "demo": True,
                "seed": "arima-daily-intelligence-v1",
            }
            return [
                self._event(
                    session=session,
                    timestamp=timestamp,
                    type=ExperienceEventType.DATA_OBJECT_CREATED,
                    source="voice_command",
                    target_chamber=ExperienceChamber.EXECUTIVE,
                    payload=payload,
                    duration_hint=2_000,
                ),
                self._event(
                    session=session,
                    timestamp=timestamp,
                    type=(
                        ExperienceEventType
                        .WATCHLIST_VISUALISATION_REQUESTED
                    ),
                    source="voice_command",
                    target_chamber=ExperienceChamber.PORTFOLIO,
                    payload={
                        "presentation": "daily",
                        "demo": True,
                        "seed": "arima-watchlist-v1",
                    },
                    duration_hint=3_000,
                ),
            ]
        if panel == "executive_briefing" and focus == "approvals":
            return [
                self._event(
                    session=session,
                    timestamp=timestamp,
                    type=(
                        ExperienceEventType.APPROVAL_VISUALISATION_REQUESTED
                    ),
                    source="voice_command",
                    target_chamber=ExperienceChamber.APPROVALS,
                    priority=ExperienceEventPriority.HIGH,
                    payload={"demo": True, "focus": "approvals"},
                    duration_hint=8_000,
                    requires_attention=True,
                )
            ]
        if panel == "growth_output":
            return [
                self._event(
                    session=session,
                    timestamp=timestamp,
                    type=ExperienceEventType.TASK_VISUALISATION_REQUESTED,
                    source="voice_command",
                    target_chamber=ExperienceChamber.GROWTH,
                    payload={
                        "task": "growth_output",
                        "status": "completed",
                        "demo": True,
                    },
                    duration_hint=2_500,
                )
            ]
        return []

    @staticmethod
    def _action_payload(action: ExecutedAction) -> dict[str, Any]:
        return {
            "name": action.name,
            "target": action.target.value,
            "success": action.success,
            "output": action.output,
            "error": action.error,
        }

    def _avatar_event(
        self,
        session: VoiceSession,
        timestamp: Any,
        state: str,
        *,
        payload: dict[str, Any] | None = None,
    ) -> ExperienceEvent:
        return self._event(
            session=session,
            timestamp=timestamp,
            type=ExperienceEventType.AVATAR_STATE_CHANGED,
            source="voice_gateway",
            payload={"state": state, **(payload or {})},
            duration_hint=500,
            dismissible=False,
        )

    def _warning_event(
        self,
        session: VoiceSession,
        timestamp: Any,
        *,
        source: str,
        message: str,
        target_chamber: ExperienceChamber | None = None,
    ) -> ExperienceEvent:
        return self._event(
            session=session,
            timestamp=timestamp,
            type=ExperienceEventType.WARNING_VISUALISATION_REQUESTED,
            source=source,
            target_chamber=target_chamber,
            priority=ExperienceEventPriority.HIGH,
            payload={"message": message},
            duration_hint=5_000,
            requires_attention=True,
        )

    @staticmethod
    def _event(
        *,
        session: VoiceSession,
        timestamp: Any,
        type: ExperienceEventType,
        source: str,
        target_chamber: ExperienceChamber | None = None,
        payload: dict[str, Any] | None = None,
        priority: ExperienceEventPriority = ExperienceEventPriority.NORMAL,
        duration_hint: int | None = None,
        dismissible: bool = True,
        requires_attention: bool = False,
    ) -> ExperienceEvent:
        return ExperienceEvent(
            session_id=session.session_id,
            correlation_id=session.correlation_id,
            timestamp=timestamp,
            type=type,
            priority=priority,
            source=source,
            target_chamber=target_chamber,
            payload=payload or {},
            duration_hint=duration_hint,
            dismissible=dismissible,
            requires_attention=requires_attention,
        )
