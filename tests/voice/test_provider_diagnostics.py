from __future__ import annotations

import asyncio

import httpx
import pytest
from pydantic import SecretStr

from app.orchestration.fallback import OrchestrationFallback
from app.orchestration.policy import OrchestrationPolicy
from app.providers.config import ProviderConfig
from app.providers.exceptions import (
    AuthenticationFailure,
    ProviderTimeout,
    ProviderUnavailable,
    RateLimitExceeded,
)
from app.providers.providers.nvidia import NvidiaProvider
from app.providers.types import (
    CompletionRequest,
    MessageRole,
    ProviderCapabilities,
    ProviderMessage,
    ProviderName,
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


def test_nvidia_boundary_emits_correlated_lifecycle_events() -> None:
    events: list[tuple[str, dict[str, object]]] = []

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "safe-id",
                "model": "safe-model",
                "choices": [{
                    "message": {"content": "safe response"},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    observer = VoiceExecutionObserver(
        None,
        "safe-session",
        sink=lambda event, payload: events.append((event, payload)),
    )
    config = ProviderConfig(
        provider=ProviderName.NVIDIA,
        default_model="safe-model",
        max_model_tokens=1_024,
        default_temperature=0.2,
        max_output_tokens=128,
        api_key=SecretStr("secret-token"),
        capabilities=ProviderCapabilities(),
    )
    request = CompletionRequest(
        model="safe-model",
        messages=(ProviderMessage(role=MessageRole.USER, content="private prompt"),),
        metadata={
            "_voice_observer": observer,
            "voice_trace_id": observer.request_id,
            "voice_session_id": observer.session_id,
            "request_mode": "conversation",
            "response_language": "en",
            "provider_attempt": 1,
        },
    )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await NvidiaProvider(config, client=client).complete(request)
        assert result.content == "safe response"

    asyncio.run(scenario())
    names = [event for event, _ in events]
    assert names == [
        "provider_attempt_start",
        "provider_request_dispatched",
        "provider_response_received",
        "provider_attempt_success",
    ]
    assert len({payload["correlation_id"] for _, payload in events}) == 1
    assert all(payload["voice_session_id"] == "safe-session" for _, payload in events)
    rendered = repr(events).lower()
    assert "private prompt" not in rendered
    assert "secret-token" not in rendered
    assert events[2][1]["status_category"] == "2xx"


def test_nvidia_boundary_failure_is_normalized_at_adapter() -> None:
    events: list[tuple[str, dict[str, object]]] = []

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "private provider response"})

    observer = VoiceExecutionObserver(
        None,
        "safe-session",
        sink=lambda event, payload: events.append((event, payload)),
    )
    config = ProviderConfig(
        provider=ProviderName.NVIDIA,
        default_model="safe-model",
        max_model_tokens=1_024,
        default_temperature=0.2,
        max_output_tokens=128,
        api_key=SecretStr("secret-token"),
        capabilities=ProviderCapabilities(),
    )
    request = CompletionRequest(
        model="safe-model",
        messages=(ProviderMessage(role=MessageRole.USER, content="private prompt"),),
        metadata={
            "_voice_observer": observer,
            "request_mode": "conversation",
            "response_language": "en",
            "provider_attempt": 1,
        },
    )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(AuthenticationFailure):
                await NvidiaProvider(config, client=client).complete(request)

    asyncio.run(scenario())
    names = [event for event, _ in events]
    assert names == [
        "provider_attempt_start",
        "provider_request_dispatched",
        "provider_response_received",
        "provider_attempt_failure",
    ]
    assert events[-1][1]["failure_class"] == "provider_auth_error"
    assert events[-1][1]["status_category"] == "4xx"
    rendered = repr(events).lower()
    assert "private provider response" not in rendered
    assert "secret-token" not in rendered
