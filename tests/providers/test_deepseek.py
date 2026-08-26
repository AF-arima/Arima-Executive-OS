from __future__ import annotations

import asyncio
import json

import httpx
from pydantic import SecretStr
import pytest

from app.providers import (
    AuthenticationFailure,
    CompletionRequest,
    DeepSeekProvider,
    ProviderCapabilities,
    ProviderConfig,
    ProviderMessage,
    ProviderName,
    ProviderTimeout,
    ProviderUnavailable,
    RateLimitExceeded,
)
from app.providers.factory import ProviderFactory
from app.core.config import Settings
from app.providers.types import MessageRole


MODEL = "deepseek-v4-flash"


def config(key: str | None = "deepseek-test-secret") -> ProviderConfig:
    return ProviderConfig(
        provider=ProviderName.DEEPSEEK,
        default_model=MODEL,
        max_model_tokens=1_000_000,
        default_temperature=0.2,
        max_output_tokens=2_048,
        api_key=SecretStr(key) if key is not None else None,
        base_url="https://api.deepseek.com",
        capabilities=ProviderCapabilities(streaming=True, tools=True, json_mode=True),
    )


def request(**metadata: object) -> CompletionRequest:
    return CompletionRequest(
        model=MODEL,
        messages=(
            ProviderMessage(role=MessageRole.SYSTEM, content="Be concise."),
            ProviderMessage(role=MessageRole.USER, content="Hello"),
        ),
        max_output_tokens=128,
        metadata=dict(metadata),
    )


def body(*, content: str = "Hello from DeepSeek", finish_reason: str = "stop") -> dict[str, object]:
    return {
        "id": "response-id",
        "model": MODEL,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": finish_reason,
        }],
        "usage": {"prompt_tokens": 8, "completion_tokens": 4},
    }


def run(provider: DeepSeekProvider, req: CompletionRequest) -> object:
    return asyncio.run(provider.complete(req))


def test_deepseek_maps_non_thinking_chat_request_and_response() -> None:
    async def handler(request_: httpx.Request) -> httpx.Response:
        payload = json.loads(request_.content)
        assert str(request_.url) == "https://api.deepseek.com/chat/completions"
        assert payload["model"] == MODEL
        assert payload["messages"] == [
            {"role": "system", "content": "Be concise."},
            {"role": "user", "content": "Hello"},
        ]
        assert payload["max_tokens"] == 128
        assert payload["stream"] is False
        assert payload["thinking"] == {"type": "disabled"}
        return httpx.Response(200, json=body())

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await DeepSeekProvider(config(), client=client).complete(request())
            assert result.provider is ProviderName.DEEPSEEK
            assert result.content == "Hello from DeepSeek"
            assert result.finish_reason == "stop"
            assert result.usage.total_tokens == 12

    asyncio.run(scenario())


@pytest.mark.parametrize("status", [400, 500, 502, 503, 504])
def test_deepseek_http_server_errors_are_unavailable(status: int) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": "redacted"})

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ProviderUnavailable):
                await DeepSeekProvider(config(), client=client).complete(request())

    asyncio.run(scenario())


@pytest.mark.parametrize("status", [401, 403])
def test_deepseek_auth_errors_are_normalized(status: int) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status)

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(AuthenticationFailure):
                await DeepSeekProvider(config(), client=client).complete(request())

    asyncio.run(scenario())


def test_deepseek_rate_limit_and_timeout_are_normalized() -> None:
    async def rate_handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    async def timeout_handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("secret transport detail")

    async def status_timeout_handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(408)

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(rate_handler)) as client:
            with pytest.raises(RateLimitExceeded):
                await DeepSeekProvider(config(), client=client).complete(request())
        async with httpx.AsyncClient(transport=httpx.MockTransport(timeout_handler)) as client:
            with pytest.raises(ProviderTimeout):
                await DeepSeekProvider(config(), client=client).complete(request())
        async with httpx.AsyncClient(transport=httpx.MockTransport(status_timeout_handler)) as client:
            with pytest.raises(ProviderTimeout):
                await DeepSeekProvider(config(), client=client).complete(request())

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("response_body", "error_match"),
    [
        ({}, "no usable output"),
        ({"choices": []}, "no usable output"),
        ({"choices": ["bad"]}, "no usable output"),
        ({"choices": [{"message": "bad"}]}, "no usable message"),
        ({"choices": [{"message": {"content": 1}, "finish_reason": "stop"}]}, "invalid content"),
        ({"choices": [{"message": {"content": "ok"}, "finish_reason": "unknown"}]}, "invalid finish reason"),
        ({"choices": [{"message": {"content": "",}, "finish_reason": "stop"}]}, "empty output"),
        ({"choices": [{"message": {"content": "",}, "finish_reason": "length"}]}, "empty output"),
        ({"choices": [{"message": {"content": "ok", "tool_calls": "bad"}, "finish_reason": "stop"}]}, "malformed tool calls"),
        ({"choices": [{"message": {"content": "", "tool_calls": []}, "finish_reason": "tool_calls"}]}, "empty tool calls"),
        ({"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}, "invalid token usage"),
    ],
)
def test_deepseek_response_parser_fails_closed(
    response_body: dict[str, object], error_match: str
) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response_body)

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ProviderUnavailable, match=error_match):
                await DeepSeekProvider(config(), client=client).complete(request())

    asyncio.run(scenario())


def test_deepseek_malformed_json_and_missing_key_fail_closed() -> None:
    async def bad_json(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json")

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(bad_json)) as client:
            with pytest.raises(ProviderUnavailable, match="invalid JSON"):
                await DeepSeekProvider(config(), client=client).complete(request())
        with pytest.raises(Exception):
            DeepSeekProvider(config(None))._api_key()

    asyncio.run(scenario())


def test_deepseek_length_with_content_is_accepted() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body(content="partial", finish_reason="length"))

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await DeepSeekProvider(config(), client=client).complete(request())
            assert result.content == "partial"
            assert result.finish_reason == "length"

    asyncio.run(scenario())


def test_deepseek_telemetry_is_sanitized() -> None:
    events: list[tuple[str, dict[str, object]]] = []
    observer = __import__("app.voice.observability", fromlist=["VoiceExecutionObserver"]).VoiceExecutionObserver(
        None, "safe-session", sink=lambda event, payload: events.append((event, payload))
    )

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body())

    async def scenario() -> None:
        metadata = {
            "_voice_observer": observer,
            "voice_trace_id": observer.request_id,
            "voice_session_id": observer.session_id,
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await DeepSeekProvider(config("secret-token"), client=client).complete(
                request(**metadata)
            )

    asyncio.run(scenario())
    rendered = repr(events)
    assert "secret-token" not in rendered
    assert "Hello from DeepSeek" not in rendered
    assert all("prompt" not in payload and "response" not in payload for _, payload in events)


def test_deepseek_factory_registration_is_configuration_gated() -> None:
    settings = Settings(
        _env_file=None,
        default_provider="nvidia",
        default_model="nvidia-model",
        deepseek_model=MODEL,
        deepseek_api_key=SecretStr("secret-token"),
    )
    factory = ProviderFactory(settings=settings)
    provider = factory.create(provider=ProviderName.DEEPSEEK)
    assert isinstance(provider, DeepSeekProvider)
    assert provider.models == (MODEL,)
    assert factory.config.default_provider is ProviderName.NVIDIA
