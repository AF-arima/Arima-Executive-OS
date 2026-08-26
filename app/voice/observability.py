from __future__ import annotations

import asyncio
import json
import logging
import sys
from collections.abc import Callable
from time import perf_counter
from typing import Any
from uuid import UUID, uuid4

import httpx

logger = logging.getLogger("arima.voice.execution")

_SINK_HANDLER_NAME = "arima-voice-execution-sink"
_SAFE_LOG_FIELDS = frozenset(
    "correlation_id voice_session_id voice_trace_id event provider model "
    "outcome attempt elapsed_ms duration_ms provider_timeout_ms failure_class "
    "exception_type status_code status_category timeout_category attempt_count "
    "providers_attempted failure_categories request_mode response_language stage "
    "parse_failure_reason finish_reason".split()
)


class _ExecutionTelemetryFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            field: getattr(record, field)
            for field in _SAFE_LOG_FIELDS
            if hasattr(record, field)
        }
        event = payload.get("event")
        if event not in _ALLOWED_EVENTS:
            event = (
                record.msg
                if isinstance(record.msg, str)
                and record.msg in _ALLOWED_EVENTS
                else None
            )
        if event is None:
            payload.pop("event", None)
        else:
            payload["event"] = event
        return json.dumps(payload, separators=(",", ":"), default=str)


def configure_execution_loggers(level: int) -> None:
    """Send sanitized execution telemetry to the process stdout sink once."""
    for name in ("arima.voice.execution", "arima.provider.execution"):
        execution_logger = logging.getLogger(name)
        execution_logger.setLevel(level)
        execution_logger.propagate = False
        if not any(
            handler.name == _SINK_HANDLER_NAME
            for handler in execution_logger.handlers
        ):
            handler = logging.StreamHandler(sys.stdout)
            handler.name = _SINK_HANDLER_NAME
            handler.setLevel(logging.NOTSET)
            handler.setFormatter(_ExecutionTelemetryFormatter())
            execution_logger.addHandler(handler)

_ALLOWED_EVENTS = frozenset(
    {
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
        "provider_attempt_failed",
        "retry_started",
        "response_received",
        "persistence_started",
        "persistence_completed",
        "voice_request_completed",
        "voice_request_timeout",
        "voice_request_failed",
        "provider_call_started",
        "provider_call_finished",
        "provider_call_failed",
        "provider_attempt_start",
        "provider_request_dispatched",
        "provider_response_received",
        "provider_attempt_success",
        "provider_attempt_failure",
        "provider_post_200_failure",
        "provider_finish_reason_invalid",
        "provider_fallback",
        "provider_fallback_exhausted",
        "orchestration_timeout",
        "orchestration_deadline_exceeded",
        "fallback_exhausted",
    }
)

_FAILURE_CLASSES = {
    "AuthenticationFailure": "provider_auth_error",
    "RateLimitExceeded": "provider_rate_limit",
    "ProviderTimeout": "provider_timeout",
    "ProviderConfigurationError": "provider_unavailable",
    "ProviderUnavailable": "provider_unavailable",
    "VoiceExecutionTimeout": "orchestration_timeout",
    "VoiceProviderUnavailable": "provider_unavailable",
    "OrchestrationFallbackExhausted": "provider_unavailable",
}


def normalized_failure_class(error: BaseException) -> str:
    """Return a safe, bounded category without inspecting exception text."""
    if isinstance(error, asyncio.TimeoutError):
        return "orchestration_timeout"
    if isinstance(error, (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.WriteTimeout)):
        return "provider_timeout"
    if isinstance(error, httpx.ConnectError):
        return "provider_connection_error"
    if isinstance(error, httpx.TransportError):
        return "provider_connection_error"
    if isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        if status in {401, 403}:
            return "provider_auth_error"
        if status == 429:
            return "provider_rate_limit"
        return "provider_http_error"
    status_value: object = getattr(error, "status_code", None)
    if isinstance(status_value, int):
        if status_value in {401, 403}:
            return "provider_auth_error"
        if status_value == 429:
            return "provider_rate_limit"
        return "provider_http_error"
    return _FAILURE_CLASSES.get(type(error).__name__, "provider_unknown_error")


def correlation_id(value: str | UUID | None = None) -> str:
    if value is not None:
        try:
            return str(UUID(str(value)))
        except (TypeError, ValueError):
            pass
    return str(uuid4())


class VoiceExecutionObserver:
    """Allowlisted, non-sensitive execution telemetry for one voice request."""

    def __init__(
        self,
        request_id: str | UUID | None,
        session_id: UUID | str,
        *,
        sink: Callable[[str, dict[str, object]], None] | None = None,
    ) -> None:
        self.request_id = correlation_id(request_id)
        self.session_id = str(session_id)
        self.started = perf_counter()
        self.sink = sink or self._log
        self.boundary_trace: list[str] | None = None

    def emit(
        self,
        event: str,
        *,
        attempt: int | None = None,
        provider: str | None = None,
        outcome: str | None = None,
        duration_ms: float | None = None,
        deadline_remaining_ms: float | None = None,
        provider_timeout_ms: float | None = None,
        failure_class: str | None = None,
        exception_type: str | None = None,
        status_code: int | None = None,
        status_category: str | None = None,
        timeout_category: str | None = None,
        attempt_count: int | None = None,
        providers_attempted: tuple[str, ...] | None = None,
        failure_categories: tuple[str, ...] | None = None,
        request_mode: str | None = None,
        response_language: str | None = None,
        model: str | None = None,
        stage: str | None = None,
        parse_failure_reason: str | None = None,
    ) -> None:
        if event not in _ALLOWED_EVENTS:
            raise ValueError(f"Unsupported voice execution event: {event}")
        payload: dict[str, object] = {
            "correlation_id": self.request_id,
            "voice_session_id": self.session_id,
            "event": event,
            "elapsed_ms": round((perf_counter() - self.started) * 1000, 2),
        }
        if attempt is not None:
            payload["attempt"] = attempt
        if provider is not None:
            payload["provider"] = provider
        if outcome is not None:
            payload["outcome"] = outcome
        safe_fields = {
            "duration_ms": duration_ms,
            "deadline_remaining_ms": deadline_remaining_ms,
            "provider_timeout_ms": provider_timeout_ms,
            "failure_class": failure_class,
            "exception_type": exception_type,
            "status_code": status_code,
            "status_category": status_category,
            "timeout_category": timeout_category,
            "attempt_count": attempt_count,
            "providers_attempted": providers_attempted,
            "failure_categories": failure_categories,
            "request_mode": request_mode,
            "response_language": response_language,
            "model": model,
            "stage": stage,
            "parse_failure_reason": parse_failure_reason,
        }
        payload.update({key: value for key, value in safe_fields.items() if value is not None})
        self.sink(event, payload)

    @staticmethod
    def _log(event: str, payload: dict[str, object]) -> None:
        logger.info(event, extra=payload)


def observer_from_context(context: Any) -> VoiceExecutionObserver | None:
    observer = context.request.metadata.get("_voice_observer")
    return observer if isinstance(observer, VoiceExecutionObserver) else None


def observer_from_metadata(metadata: Any) -> VoiceExecutionObserver | None:
    if not isinstance(metadata, dict):
        return None
    observer = metadata.get("_voice_observer")
    return observer if isinstance(observer, VoiceExecutionObserver) else None


def http_status_category(status_code: int | None) -> str | None:
    if not isinstance(status_code, int) or status_code < 100:
        return None
    return f"{status_code // 100}xx"
