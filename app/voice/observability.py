from __future__ import annotations

import logging
from collections.abc import Callable
from time import perf_counter
from typing import Any
from uuid import UUID, uuid4

logger = logging.getLogger("arima.voice.execution")

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
    }
)


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

    def emit(
        self,
        event: str,
        *,
        attempt: int | None = None,
        provider: str | None = None,
        outcome: str | None = None,
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
        self.sink(event, payload)

    @staticmethod
    def _log(event: str, payload: dict[str, object]) -> None:
        logger.info(event, extra=payload)


def observer_from_context(context: Any) -> VoiceExecutionObserver | None:
    observer = context.request.metadata.get("_voice_observer")
    return observer if isinstance(observer, VoiceExecutionObserver) else None
