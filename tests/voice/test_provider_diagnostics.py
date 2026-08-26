from __future__ import annotations

import asyncio

import httpx
import pytest

from app.orchestration.fallback import OrchestrationFallback
from app.orchestration.policy import OrchestrationPolicy
from app.providers.exceptions import (
    AuthenticationFailure,
    ProviderTimeout,
    ProviderUnavailable,
    RateLimitExceeded,
)
from app.voice.observability import (
    VoiceExecutionObserver,
    normalized_failure_class,
)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ProviderTimeout("safe"), "provider_timeout"),
        (AuthenticationFailure("safe"), "provider_auth_error"),
        (RateLimitExceeded("safe"), "provider_rate_limit"),
        (ProviderUnavailable("safe"), "provider_unavailable"),
        (asyncio.TimeoutError(), "orchestration_timeout"),
        (httpx.ConnectError("safe"), "provider_connection_error"),
    ],
)
def test_failure_classes_are_bounded(error: BaseException, expected: str) -> None:
    assert normalized_failure_class(error) == expected


def test_http_failure_status_is_safe_metadata() -> None:
    error = AuthenticationFailure("private provider detail")
    error.status_code = 403
    assert normalized_failure_class(error) == "provider_auth_error"


def test_observer_preserves_safe_metadata_and_trace() -> None:
    events: list[tuple[str, dict[str, object]]] = []
    observer = VoiceExecutionObserver(
        None,
        "voice-session",
        sink=lambda event, payload: events.append((event, payload)),
    )
    observer.emit(
        "provider_call_started",
        provider="nvidia",
        model="safe-model",
        request_mode="conversation",
        response_language="ru",
        provider_timeout_ms=15000,
    )
    observer.emit(
        "provider_call_failed",
        provider="nvidia",
        model="safe-model",
        failure_class="provider_timeout",
        exception_type="ProviderTimeout",
        status_code=503,
    )

    assert len({payload["correlation_id"] for _, payload in events}) == 1
    rendered = repr(events).lower()
    assert "private" not in rendered
    assert "token" not in rendered
    assert "authorization" not in rendered
    assert events[1][1]["failure_class"] == "provider_timeout"


def test_fallback_emits_failure_and_exhaustion_without_private_detail() -> None:
    events: list[tuple[str, dict[str, object]]] = []
    observer = VoiceExecutionObserver(
        None,
        "voice-session",
        sink=lambda event, payload: events.append((event, payload)),
    )

    async def operation() -> str:
        raise ProviderTimeout("secret provider response")

    async def scenario() -> None:
        with pytest.raises(Exception):
            await OrchestrationFallback(
                OrchestrationPolicy(maximum_retries=1)
            ).retry(
                operation,
                observer=observer.emit,
                provider_name="nvidia",
            )

    asyncio.run(scenario())
    names = [event for event, _ in events]
    assert "provider_attempt_failed" in names
    assert "fallback_exhausted" in names
    assert all("secret provider response" not in repr(payload) for _, payload in events)
    assert all(payload["correlation_id"] == observer.request_id for _, payload in events)
