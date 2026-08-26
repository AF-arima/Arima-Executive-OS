from __future__ import annotations

import asyncio

import httpx
import pytest
from pydantic import SecretStr

from app.orchestration.fallback import OrchestrationFallback
from app.orchestration.exceptions import OrchestrationFallbackExhausted
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


def _nvidia_provider(client: httpx.AsyncClient | None = None) -> NvidiaProvider:
    return NvidiaProvider(
        ProviderConfig(
            provider=ProviderName.NVIDIA,
            default_model="safe-model",
            max_model_tokens=1_024,
            default_temperature=0.2,
            max_output_tokens=128,
            api_key=SecretStr("secret-token"),
            capabilities=ProviderCapabilities(),
        ),
        client=client,
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


def test_request_timeout_uses_remaining_deadline_and_shared_clock() -> None:
    provider = _nvidia_provider()

    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        request = CompletionRequest(
            model="safe-model",
            messages=(ProviderMessage(role=MessageRole.USER, content="safe"),),
            metadata={"execution_deadline_monotonic": loop.time() + 20},
        )
        timeout = provider._request_timeout(request)
        assert 17.0 < timeout <= 18.0

    asyncio.run(scenario())


def test_insufficient_budget_fails_before_http_and_logs_safe_warning(caplog: pytest.LogCaptureFixture) -> None:
    provider = _nvidia_provider()

    async def scenario() -> None:
        loop = asyncio.get_running_loop()
        request = CompletionRequest(
            model="safe-model",
            messages=(ProviderMessage(role=MessageRole.USER, content="safe"),),
            metadata={
                "execution_deadline_monotonic": loop.time() + 4,
                "voice_session_id": "safe-session",
            },
        )
        with pytest.raises(ProviderTimeout):
            provider._request_timeout(request)

    with caplog.at_level("WARNING", logger="arima.provider.execution"):
        asyncio.run(scenario())
    record = next(record for record in caplog.records if record.levelname == "WARNING")
    assert record.voice_session_id == "safe-session"
    assert 0 < record.deadline_remaining_ms < 4_000


def test_preemptive_and_in_request_provider_timeout_follow_same_fallback_path(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def run(
        request: CompletionRequest,
        provider: NvidiaProvider,
    ) -> list[tuple[str, dict[str, object]]]:
        events: list[tuple[str, dict[str, object]]] = []
        observer = VoiceExecutionObserver(
            None,
            "safe-session",
            sink=lambda event, payload: events.append((event, payload)),
        )
        request.metadata["_voice_observer"] = observer

        async def operation() -> None:
            request.metadata["provider_attempt"] = int(
                request.metadata.get("provider_attempt", 0)
            ) + 1
            await provider.complete(request)

        with pytest.raises(OrchestrationFallbackExhausted):
            await OrchestrationFallback(
                OrchestrationPolicy(maximum_retries=1)
            ).retry(operation, observer=observer.emit, provider_name="nvidia")
        return events

    async def scenario() -> None:
        preemptive_posts = 0

        async def preemptive_handler(_: httpx.Request) -> httpx.Response:
            nonlocal preemptive_posts
            preemptive_posts += 1
            return httpx.Response(200, json={})

        preemptive_request = CompletionRequest(
            model="safe-model",
            messages=(ProviderMessage(role=MessageRole.USER, content="safe"),),
            metadata={
                "execution_deadline_monotonic": asyncio.get_running_loop().time() + 4,
                "provider_attempt": 0,
            },
        )
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(preemptive_handler)
        ) as preemptive_client:
            preemptive_events = await run(
                preemptive_request, _nvidia_provider(preemptive_client)
            )

        in_request_posts = 0

        async def in_request_handler(_: httpx.Request) -> httpx.Response:
            nonlocal in_request_posts
            in_request_posts += 1
            raise httpx.ReadTimeout("transport timeout")

        in_request_request = CompletionRequest(
            model="safe-model",
            messages=(ProviderMessage(role=MessageRole.USER, content="safe"),),
            metadata={"provider_attempt": 0},
        )
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(in_request_handler)
        ) as in_request_client:
            in_request_events = await run(
                in_request_request, _nvidia_provider(in_request_client)
            )

        def comparable(
            events: list[tuple[str, dict[str, object]]],
        ) -> list[tuple[str, object, object, object, object]]:
            return [
                (
                    event,
                    payload.get("attempt"),
                    payload.get("outcome"),
                    payload.get("failure_class"),
                    payload.get("exception_type"),
                )
                for event, payload in events
            ]

        assert comparable(preemptive_events) == comparable(in_request_events)
        assert preemptive_posts == 0
        assert in_request_posts == 2
        assert all("transport timeout" not in repr(payload) for _, payload in in_request_events)

    with caplog.at_level("WARNING", logger="arima.provider.execution"):
        asyncio.run(scenario())
    warnings = [
        record
        for record in caplog.records
        if record.name == "arima.provider.execution"
        and record.message == "provider_timeout_budget_insufficient"
    ]
    assert len(warnings) == 2



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


@pytest.mark.parametrize(
    ("body", "stage"),
    [
        ({"choices": []}, "completion_parse"),
        ({"choices": [{"message": {}}]}, "completion_parse"),
        (
            {"choices": [{"message": {"content": ""}, "finish_reason": "stop"}]},
            "completion_parse",
        ),
        (
            {
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            },
            "usage_parse",
        ),
        (
            {
                "choices": [{"message": {"content": "partial"}, "finish_reason": "length"}],
            },
            "usage_parse",
        ),
        (
            {
                "choices": [{"message": {"content": ""}, "finish_reason": "length"}],
            },
            "completion_parse",
        ),
    ],
)
def test_post_200_failures_are_staged_without_private_details(
    body: dict[str, object], stage: str
) -> None:
    events: list[tuple[str, dict[str, object]]] = []

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

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
        metadata={"_voice_observer": observer, "provider_attempt": 1},
    )

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            with pytest.raises(ProviderUnavailable):
                await NvidiaProvider(config, client=client).complete(request)

    asyncio.run(scenario())
    failure = next(
        payload
        for event, payload in events
        if event == "provider_post_200_failure"
    )
    assert failure["stage"] == stage
    rendered = repr(events)
    assert "private prompt" not in rendered
    assert "secret-token" not in rendered
    assert "provider response" not in rendered


def test_post_200_body_decode_failure_is_staged() -> None:
    events: list[tuple[str, dict[str, object]]] = []

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json")

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
        metadata={"_voice_observer": observer, "provider_attempt": 1},
    )

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            with pytest.raises(ProviderUnavailable):
                await NvidiaProvider(config, client=client).complete(request)

    asyncio.run(scenario())
    failure = next(payload for event, payload in events if event == "provider_post_200_failure")
    assert failure["stage"] == "body_decode"


def test_invalid_finish_reason_logs_only_safe_value_and_correlation() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": "sensitive completion"},
                        "finish_reason": "unexpected_reason",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    observer = VoiceExecutionObserver(None, "safe-session")
    request = CompletionRequest(
        model="safe-model",
        messages=(ProviderMessage(role=MessageRole.USER, content="sensitive prompt"),),
        metadata={
            "_voice_observer": observer,
            "provider_attempt": 1,
            "voice_trace_id": observer.request_id,
            "voice_session_id": observer.session_id,
        },
    )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ProviderUnavailable):
                await _nvidia_provider(client).complete(request)

    with pytest.MonkeyPatch.context() as monkeypatch:
        records: list[object] = []
        monkeypatch.setattr(
            "app.providers.providers.nvidia.logger.warning",
            lambda event, **kwargs: (
                records.append(kwargs["extra"])
                if event == "provider_finish_reason_invalid"
                else None
            ),
        )
        asyncio.run(scenario())

    assert records == [
        {
            "correlation_id": observer.request_id,
            "voice_session_id": observer.session_id,
            "finish_reason": "unexpected_reason",
            "summary": (
                "provider_finish_reason_invalid "
                f"session={observer.session_id} "
                f"correlation={observer.request_id} finish_reason=unexpected_reason"
            ),
        }
    ]
    assert "sensitive completion" not in repr(records)
    assert "sensitive prompt" not in repr(records)


def test_invalid_finish_reason_emits_plain_safe_summary() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": "private completion"},
                        "finish_reason": "unexpected_reason",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            },
        )

    observer = VoiceExecutionObserver(None, "safe-session")
    request = CompletionRequest(
        model="safe-model",
        messages=(ProviderMessage(role=MessageRole.USER, content="private prompt"),),
        metadata={
            "_voice_observer": observer,
            "provider_attempt": 1,
            "voice_trace_id": observer.request_id,
            "voice_session_id": observer.session_id,
        },
    )
    plain_records: list[tuple[str, dict[str, object]]] = []

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ProviderUnavailable):
                await _nvidia_provider(client).complete(request)

    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setattr(
            "app.providers.providers.nvidia.plain_logger.warning",
            lambda message, **kwargs: plain_records.append((message, kwargs)),
        )
        asyncio.run(scenario())

    assert plain_records == [
        (
            (
                "provider_finish_reason_invalid "
                f"session={observer.session_id} "
                f"correlation={observer.request_id} finish_reason=unexpected_reason"
            ),
            {},
        )
    ]
    assert "private completion" not in repr(plain_records)
    assert "private prompt" not in repr(plain_records)


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ({}, "choices_missing_or_invalid"),
        ({"choices": "invalid"}, "choices_missing_or_invalid"),
        ({"choices": ["invalid"]}, "choice_invalid"),
        ({"choices": [{"message": "invalid"}]}, "message_missing_or_invalid"),
        (
            {"choices": [{"message": {"content": 1}, "finish_reason": "stop"}]},
            "content_invalid",
        ),
        (
            {"choices": [{"message": {"content": "ok", "tool_calls": "invalid"}, "finish_reason": "stop"}]},
            "tool_calls_not_list",
        ),
        (
            {
                "choices": [{"message": {"content": "ok"}, "finish_reason": "invalid"}]
            },
            "finish_reason_invalid",
        ),
        (
            {"choices": [{"message": {"content": ""}, "finish_reason": "stop"}]},
            "empty_completion",
        ),
        (
            {"choices": [{"message": {"content": "ok"}, "finish_reason": "tool_calls"}]},
            "empty_tool_calls",
        ),
        (
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [{"id": "call", "function": {}}],
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            },
            "tool_call_function_invalid",
        ),
        (
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [{"function": {"name": "tool"}}],
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            },
            "tool_call_missing_id",
        ),
        (
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {"id": "call", "function": {"name": "tool", "arguments": 1}}
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            },
            "tool_arguments_invalid_type",
        ),
        (
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {"id": "call", "function": {"name": "tool", "arguments": "not-json"}}
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            },
            "tool_arguments_invalid_json",
        ),
        (
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {"id": "call", "function": {"name": "tool", "arguments": "[]"}}
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            },
            "tool_arguments_not_object",
        ),
    ],
)
def test_completion_parse_failures_have_static_safe_reasons(
    body: dict[str, object], reason: str
) -> None:
    with pytest.raises(ProviderUnavailable) as captured:
        NvidiaProvider._completion(body)
    assert getattr(captured.value, "parse_failure_reason") == reason


def test_post_200_success_emit_failure_is_staged_without_message() -> None:
    events: list[tuple[str, dict[str, object]]] = []

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "safe"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    def sink(event: str, payload: dict[str, object]) -> None:
        events.append((event, payload))
        if event == "provider_attempt_success":
            raise RuntimeError("private success sink detail")

    observer = VoiceExecutionObserver(None, "safe-session", sink=sink)
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
        metadata={"_voice_observer": observer, "provider_attempt": 1},
    )

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            with pytest.raises(RuntimeError, match="private success sink detail"):
                await NvidiaProvider(config, client=client).complete(request)

    asyncio.run(scenario())
    failure = next(payload for event, payload in events if event == "provider_post_200_failure")
    assert failure["stage"] == "success_emit"
    assert "private success sink detail" not in repr(failure)
