from __future__ import annotations

import asyncio
import io
import json
import logging
import sys
from uuid import UUID

import pytest

from app.voice.observability import (
    VoiceExecutionObserver,
    configure_execution_loggers,
)


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


def test_execution_loggers_sink_is_safe_and_idempotent(monkeypatch) -> None:
    output = io.StringIO()
    loggers = [logging.getLogger(name) for name in (
        "arima.voice.execution", "arima.provider.execution"
    )]
    for logger in loggers:
        monkeypatch.setattr(logger, "handlers", [])
    configure_execution_loggers(logging.INFO)
    configure_execution_loggers(logging.INFO)
    sinks = []
    for logger in loggers:
        [sink] = logger.handlers
        assert sink.name == "arima-voice-execution-sink"
        assert sink.stream is sys.stdout
        assert logger.propagate is False
        sink.setStream(output)
        sinks.append(sink)
    VoiceExecutionObserver(None, "session-123").emit(
        "provider_attempt_start", provider="nvidia", attempt=1, outcome="started"
    )
    loggers[1].warning(
        "provider_call_failed",
        extra={"provider": "nvidia", "failure_class": "provider_timeout"},
    )
    record = logging.LogRecord(
        "arima.voice.execution", logging.ERROR, __file__, 1,
        "secret exception details", (), None,
    )
    record.exc_info = (RuntimeError, RuntimeError("secret exception details"), None)
    assert "secret exception details" not in sinks[0].format(record)

    lines = [line for line in output.getvalue().splitlines() if line]
    assert len(lines) == 2
    records = [json.loads(line) for line in lines]
    assert records[0]["event"] == "provider_attempt_start"
    assert records[1]["event"] == "provider_call_failed"
    assert all(record["provider"] == "nvidia" for record in records)
    assert all(
        "prompt" not in record and "response" not in record
        for record in records
    )


def test_sink_excludes_unallowlisted_fields_and_raw_message(monkeypatch) -> None:
    logger = logging.getLogger("arima.voice.execution")
    monkeypatch.setattr(logger, "handlers", [])
    configure_execution_loggers(logging.INFO)
    [handler] = logger.handlers
    output = io.StringIO()
    handler.setStream(output)
    record = logging.makeLogRecord(
        {
            "name": logger.name,
            "levelno": logging.INFO,
            "levelname": "INFO",
            "msg": "UNAPPROVED_FIELD_SHOULD_NOT_APPEAR",
            "event": "provider_attempt_start",
            "correlation_id": "trace-123",
            "unapproved_field": "UNAPPROVED_FIELD_SHOULD_NOT_APPEAR",
        }
    )
    handler.emit(record)
    rendered = output.getvalue()
    assert "provider_attempt_start" in rendered
    assert "trace-123" in rendered
    assert "UNAPPROVED_FIELD_SHOULD_NOT_APPEAR" not in rendered


def test_repeated_configuration_preserves_handler_and_stdout(monkeypatch) -> None:
    loggers = [
        logging.getLogger("arima.voice.execution"),
        logging.getLogger("arima.provider.execution"),
    ]
    for logger in loggers:
        monkeypatch.setattr(logger, "handlers", [])
    configure_execution_loggers(logging.INFO)
    original = [(logger.handlers[0], logger.handlers[0].stream) for logger in loggers]
    configure_execution_loggers(logging.INFO)
    for logger, (handler, stream) in zip(loggers, original):
        assert logger.handlers == [handler]
        assert handler.stream is stream is sys.stdout
