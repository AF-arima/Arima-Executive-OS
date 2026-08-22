from __future__ import annotations

import asyncio
from uuid import UUID

import pytest

from app.voice.observability import VoiceExecutionObserver


def test_voice_observer_allowlists_correlation_and_stage_metadata() -> None:
    events: list[tuple[str, dict[str, object]]] = []
    observer = VoiceExecutionObserver(
        "not-a-valid-id",
        "session-123",
        sink=lambda event, payload: events.append((event, payload)),
    )

    for event in (
        "voice_request_started",
        "session_validated",
        "workspace_resolved",
        "conversation_resolved",
        "context_started",
        "context_completed",
        "orchestration_started",
        "provider_selected",
        "provider_attempt_started",
        "provider_attempt_completed",
        "response_received",
        "persistence_started",
        "persistence_completed",
        "voice_request_completed",
    ):
        observer.emit(event, attempt=1, provider="gemini", outcome="success")

    assert UUID(observer.request_id)
    assert len(events) == 14
    allowed = {
        "correlation_id",
        "voice_session_id",
        "event",
        "elapsed_ms",
        "attempt",
        "provider",
        "outcome",
    }
    assert all(set(payload) <= allowed for _, payload in events)
    assert all("prompt" not in payload and "response" not in payload for _, payload in events)


@pytest.mark.asyncio
async def test_provider_cancellation_emits_failure_without_success() -> None:
    events: list[tuple[str, dict[str, object]]] = []
    observer = VoiceExecutionObserver(
        None,
        "session-123",
        sink=lambda event, payload: events.append((event, payload)),
    )

    observer.emit(
        "provider_attempt_started",
        attempt=1,
        provider="gemini",
        outcome="started",
    )
    observer.emit(
        "provider_attempt_failed",
        attempt=1,
        provider="gemini",
        outcome="cancelled",
    )
    await asyncio.sleep(0)

    assert [event for event, _ in events] == [
        "provider_attempt_started",
        "provider_attempt_failed",
    ]
    assert not any(event.endswith("completed") for event, _ in events)


def test_timeout_event_contains_no_sensitive_values() -> None:
    events: list[tuple[str, dict[str, object]]] = []
    observer = VoiceExecutionObserver(
        None,
        "session-123",
        sink=lambda event, payload: events.append((event, payload)),
    )
    observer.emit("voice_request_timeout", outcome="timeout")

    event, payload = events[0]
    assert event == "voice_request_timeout"
    assert payload["outcome"] == "timeout"
    assert "secret" not in repr(payload).lower()
    assert "prompt" not in repr(payload).lower()
    assert "response" not in repr(payload).lower()
