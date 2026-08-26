import asyncio
from dataclasses import replace
import json

import httpx
from pydantic import SecretStr
import pytest

from app.core.config import Settings
from app.providers.config import ProviderConfig
from app.providers.exceptions import (
    AuthenticationFailure,
    ProviderConfigurationError,
    ProviderTimeout,
    ProviderUnavailable,
    RateLimitExceeded,
)
from app.providers.factory import ProviderFactory
from app.providers.providers.nvidia import NvidiaProvider
from app.providers.types import (
    CompletionRequest,
    MessageRole,
    ProviderCapabilities,
    ProviderMessage,
    ProviderToolCall,
    ProviderToolResult,
    ProviderName,
    ProviderStatus,
)
from app.voice.observability import VoiceExecutionObserver

VERIFIED_MODEL = "nvidia/nvidia-nemotron-nano-9b-v2"


def configuration(*, api_key: str | None = "test-nvidia-token") -> ProviderConfig:
    return ProviderConfig(
        provider=ProviderName.NVIDIA,
        default_model=VERIFIED_MODEL,
        max_model_tokens=131_072,
        default_temperature=0.2,
        max_output_tokens=2_048,
        api_key=SecretStr(api_key) if api_key is not None else None,
        capabilities=ProviderCapabilities(
            streaming=True,
            reasoning=True,
        ),
    )


def request() -> CompletionRequest:
    return CompletionRequest(
        model=VERIFIED_MODEL,
        messages=(
            ProviderMessage(
                role=MessageRole.SYSTEM,
                content="Follow the governed Arima instructions.",
            ),
            ProviderMessage(
                role=MessageRole.USER,
                content="Give the approved voice response.",
            ),
        ),
        temperature=0.2,
        max_output_tokens=512,
    )


def test_nvidia_native_tool_call_and_tool_result_payload() -> None:
    async def handler(http_request: httpx.Request) -> httpx.Response:
        body = json.loads(http_request.content)
        assert body["tool_choice"] == "auto"
        assert body["chat_template_kwargs"] == {"enable_thinking": False}
        assert body["tools"][0]["function"]["name"] == "email_list_recent"
        assert body["messages"][-1] == {
            "role": "tool",
            "tool_call_id": "call-1",
            "name": "email_list_recent",
            "content": '{"messages":[]}',
        }
        return httpx.Response(
            200,
            json={
                "model": VERIFIED_MODEL,
                "choices": [{
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{
                            "id": "call-2",
                            "type": "function",
                            "function": {
                                "name": "email_list_recent",
                                "arguments": '{"limit":1}',
                            },
                        }],
                    },
                    "finish_reason": "tool_calls",
                }],
                "usage": {"prompt_tokens": 2, "completion_tokens": 3},
            },
        )

    provider = NvidiaProvider(
        configuration(), client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    async def run():
        return await provider.complete(CompletionRequest(
            model=VERIFIED_MODEL,
            messages=(
                ProviderMessage(role=MessageRole.USER, content="List mail"),
                ProviderMessage(
                    role=MessageRole.ASSISTANT,
                    tool_calls=(ProviderToolCall("email_list_recent", "call-1", {"limit": 1}),),
                ),
                ProviderMessage(
                    role=MessageRole.TOOL,
                    tool_result=ProviderToolResult(
                        call_id="call-1",
                        wire_name="email_list_recent",
                        serialized_result='{"messages":[]}',
                    ),
                ),
            ),
            tools=({
                "type": "function",
                "function": {
                    "name": "email_list_recent",
                    "description": "List recent mail",
                    "parameters": {"type": "object"},
                },
            },),
            metadata={
                "tool_choice": "auto",
                "chat_template_kwargs": {"enable_thinking": False},
            },
        ))
    result = asyncio.run(run())
    assert result.finish_reason == "tool_calls"
    assert result.tool_calls[0].call_id == "call-2"


def test_nvidia_chat_adapter_uses_verified_server_side_contract() -> None:
    async def handler(http_request: httpx.Request) -> httpx.Response:
        assert http_request.url == (
            "https://integrate.api.nvidia.com/v1/chat/completions"
        )
        assert http_request.headers["Authorization"] == (
            "Bearer test-nvidia-token"
        )
        assert http_request.headers["Accept"] == "application/json"
        assert json.loads(http_request.content) == {
            "model": VERIFIED_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": "Follow the governed Arima instructions.",
                },
                {
                    "role": "user",
                    "content": "Give the approved voice response.",
                },
            ],
            "temperature": 0.2,
            "max_tokens": 512,
            "stream": False,
        }
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl_test",
                "model": VERIFIED_MODEL,
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": "Approved NVIDIA voice response.",
                            "reasoning_content": "private reasoning",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 14,
                    "completion_tokens": 5,
                    "total_tokens": 19,
                },
            },
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            provider = NvidiaProvider(configuration(), client=client)
            result = await provider.complete(request())

        assert result.provider is ProviderName.NVIDIA
        assert result.model == VERIFIED_MODEL
        assert result.content == "Approved NVIDIA voice response."
        assert result.finish_reason == "stop"
        assert result.usage.input_tokens == 14
        assert result.usage.output_tokens == 5
        assert "reasoning_content" not in result.metadata
        assert "test-nvidia-token" not in repr(result)

    asyncio.run(scenario())


def test_nvidia_fake_boundary_preserves_observer_trace() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": "Approved response."},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    async def scenario() -> None:
        trace: list[str] = []
        events: list[str] = []
        observer = VoiceExecutionObserver(
            "00000000-0000-4000-8000-000000000001",
            "00000000-0000-4000-8000-000000000002",
            sink=lambda event, _: events.append(event),
        )
        metadata = {
            **request().metadata,
            "_voice_observer": observer,
            "_boundary_trace": trace,
        }
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            provider = NvidiaProvider(configuration(), client=client)
            result = await provider.complete(
                replace(request(), metadata=metadata)
            )

        assert result.content == "Approved response."
        assert trace == ["E_PROVIDER_ENTRY", "F_PROVIDER_RETURN"]
        assert events == [
            "provider_attempt_start",
            "provider_request_dispatched",
            "provider_response_received",
            "provider_attempt_success",
        ]

    asyncio.run(scenario())


def test_nvidia_configuration_health_and_factory_selection() -> None:
    missing = NvidiaProvider(configuration(api_key=None))
    health = asyncio.run(missing.health())
    assert health.status is ProviderStatus.UNAVAILABLE
    assert health.available is False

    settings = Settings(
        _env_file=None,
        default_provider="nvidia",
        default_model=VERIFIED_MODEL,
        nvidia_api_key=SecretStr("test-nvidia-token"),
    )
    registry = ProviderFactory(settings=settings).build_registry()
    assert tuple(provider.provider for provider in registry.list()) == (
        ProviderName.NVIDIA,
    )

    disabled = Settings(
        _env_file=None,
        ai_execution_enabled=False,
        default_provider="nvidia",
        default_model=VERIFIED_MODEL,
        nvidia_api_key=SecretStr("test-nvidia-token"),
    )
    assert ProviderFactory(settings=disabled).build_registry().list() == ()


@pytest.mark.parametrize(
    ("status", "exception"),
    [
        (401, AuthenticationFailure),
        (403, AuthenticationFailure),
        (429, RateLimitExceeded),
        (500, ProviderUnavailable),
    ],
)
def test_nvidia_http_failures_are_classified_and_redacted(
    status: int,
    exception: type[Exception],
) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status,
            json={"error": {"message": "test-nvidia-token leaked"}},
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            provider = NvidiaProvider(configuration(), client=client)
            with pytest.raises(exception) as captured:
                await provider.complete(request())
        assert "test-nvidia-token" not in str(captured.value)

    asyncio.run(scenario())


def test_nvidia_timeout_is_fail_closed() -> None:
    async def handler(http_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("test-nvidia-token leaked", request=http_request)

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            provider = NvidiaProvider(configuration(), client=client)
            with pytest.raises(ProviderTimeout) as captured:
                await provider.complete(request())
        assert str(captured.value) == "NVIDIA request timed out"

    asyncio.run(scenario())


def test_nvidia_invalid_success_response_fails_closed() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            provider = NvidiaProvider(configuration(), client=client)
            with pytest.raises(ProviderUnavailable, match="no usable output"):
                await provider.complete(request())

    asyncio.run(scenario())


def test_nvidia_truncated_response_and_unsupported_temperature_fail_closed(
) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": "Partial response"},
                        "finish_reason": "length",
                    }
                ],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            provider = NvidiaProvider(configuration(), client=client)
            with pytest.raises(
                ProviderUnavailable,
                match=r"incomplete response \(finish_reason=length\)",
            ):
                await provider.complete(request())
            with pytest.raises(
                ProviderConfigurationError, match="temperature"
            ):
                await provider.complete(
                    CompletionRequest(
                        model=VERIFIED_MODEL,
                        messages=request().messages,
                        temperature=1.1,
                    )
                )

    asyncio.run(scenario())
