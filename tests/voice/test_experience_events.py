import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from app.experience.mapper import ExperienceEventMapper
from app.experience.schemas import (
    ExperienceChamber,
    ExperienceEvent,
    ExperienceEventPriority,
    ExperienceEventType,
)
from app.orchestration.factory import OrchestrationFactory
from app.orchestration.schemas import OrchestrationRequest
from app.voice.events import VoiceEventType
from app.voice.schemas import VoiceEvent, VoiceSession
from tests.database.helpers import sqlite_session
from tests.orchestration.helpers import make_context

NOW = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)


def make_session() -> VoiceSession:
    return VoiceSession(
        user_id=uuid4(),
        language="en",
        locale="en-GB",
        timezone="Europe/London",
        created_at=NOW,
        updated_at=NOW,
    )


def test_experience_event_schema_has_the_required_envelope() -> None:
    session = make_session()
    event = ExperienceEvent(
        session_id=session.session_id,
        correlation_id=session.correlation_id,
        timestamp=NOW,
        type=ExperienceEventType.SYSTEM_PULSE,
        priority=ExperienceEventPriority.NORMAL,
        source="test",
        target_chamber=ExperienceChamber.HEALTH,
        payload={"available": True},
        duration_hint=1_000,
        dismissible=False,
        requires_attention=False,
    )

    assert event.event_id
    assert event.session_id == session.session_id
    assert event.correlation_id == session.correlation_id
    assert event.payload == {"available": True}


def test_voice_events_map_to_avatar_chamber_approval_and_demo_objects() -> None:
    session = make_session()
    mapper = ExperienceEventMapper()
    voice_events = [
        VoiceEvent(
            event=VoiceEventType.THINKING_STARTED,
            sequence=0,
            timestamp=NOW,
        ),
        VoiceEvent(
            event=VoiceEventType.NAVIGATION_REQUESTED,
            sequence=1,
            timestamp=NOW,
            data={"path": "/quant-research", "label": "Quant Research"},
        ),
        VoiceEvent(
            event=VoiceEventType.PANEL_REQUESTED,
            sequence=2,
            timestamp=NOW,
            data={"panel": "executive_briefing", "focus": "today"},
        ),
        VoiceEvent(
            event=VoiceEventType.APPROVAL_REQUIRED,
            sequence=3,
            timestamp=NOW,
            data={"title": "Approval required"},
        ),
    ]

    events = mapper.from_gateway_events(
        session=session,
        voice_events=voice_events,
    )

    assert any(
        event.type is ExperienceEventType.AVATAR_STATE_CHANGED
        and event.payload["state"] == "thinking"
        for event in events
    )
    assert any(
        event.type is ExperienceEventType.CHAMBER_TRANSITION_REQUESTED
        and event.target_chamber is ExperienceChamber.QUANT
        for event in events
    )
    watchlist = next(
        event
        for event in events
        if event.type is ExperienceEventType.WATCHLIST_VISUALISATION_REQUESTED
    )
    assert watchlist.payload == {
        "presentation": "daily",
        "demo": True,
        "seed": "arima-watchlist-v1",
    }
    approval = next(
        event
        for event in events
        if event.type is ExperienceEventType.APPROVAL_VISUALISATION_REQUESTED
    )
    assert approval.requires_attention is True
    assert approval.target_chamber is ExperienceChamber.APPROVALS


def test_orchestration_results_map_project_and_background_outcomes() -> None:
    async def scenario() -> None:
        mapper = ExperienceEventMapper()
        cases = (
            (
                "Show project status",
                ExperienceEventType.TASK_VISUALISATION_REQUESTED,
            ),
            (
                "Run quant research",
                ExperienceEventType.BACKGROUND_JOB_COMPLETED,
            ),
        )
        for content, expected_type in cases:
            async with sqlite_session() as session:
                context = await make_context(
                    session,
                    OrchestrationRequest(content=content),
                )
                result = await OrchestrationFactory(session).create().execute(
                    context
                )
                voice_session = make_session().model_copy(
                    update={"correlation_id": result.correlation_id}
                )
                events = mapper.from_orchestration_result(
                    voice_session,
                    result,
                )
                assert any(
                    event.type is expected_type for event in events
                )
                assert all(
                    event.correlation_id == result.correlation_id
                    for event in events
                )

    asyncio.run(scenario())
