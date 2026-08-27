from __future__ import annotations

import asyncio
import json

import httpx
from pydantic import SecretStr
import pytest

from app.core.config import Settings
from app.providers import (
    AuthenticationFailure,
    CompletionRequest,
    GroqProvider,
    ProviderCapabilities,
    ProviderConfig,
    ProviderFactory,
    ProviderMessage,
    ProviderName,
    ProviderTimeout,
    ProviderUnavailable,
    RateLimitExceeded,
)
from app.providers.types import MessageRole
from app.voice.observability import VoiceExecutionObserver


MODEL = "openai/gpt-oss-20b"


def config(key: str | None = "groq-test-secret") -> ProviderConfig:
    return ProviderConfig(
        provider=ProviderName.GROQ,
        default_model=MODEL,
        max_model_tokens=131_072,
        default_temperature=0.2,
        max_output_tokens=2_048,
        api_key=SecretStr(key) if key is not None else None,
        base_url="https://api.groq.com/openai/v1",
        capabilities=ProviderCapabilities(tools=True, json_mode=True),
    )


def request(
    *,
    tools: tuple[dict[str, object], ...] = (),
    json_mode: bool = False,
    **metadata: object,
) -> CompletionRequest:
    return CompletionRequest(
        model=MODEL,
        messages=(
            ProviderMessage(role=MessageRole.SYSTEM, content="Be concise."),
            ProviderMessage(role=MessageRole.USER, content="Hello"),
        ),
        max_output_tokens=128,
        tools=tools,
        json_mode=json_mode,
        metadata=dict(metadata),
    )


def body(*, content: str = "GROQ_OK", finish_reason: str = "stop") -> dict[str, object]:
    return {
        "id": "response-id",
        "model": MODEL,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": finish_reason,
        }],
        "usage": {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12},
    }


def test_groq_maps_chat_request_and_response() -> None:
    async def handler(request_: httpx.Request) -> httpx.Response:
        payload = json.loads(request_.content)
        assert str(request_.url) == "https://api.groq.com/openai/v1/chat/completions"
        assert payload["model"] == MODEL
        assert payload["max_completion_tokens"] == 128
        assert payload["stream"] is False
        assert payload["include_reasoning"] is False
        return httpx.Response(200, json=body())

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await GroqProvider(config(), client=client).complete(request())
            assert result.provider is ProviderName.GROQ
            assert result.content == "GROQ_OK"
            assert result.finish_reason == "stop"
            assert result.usage.total_tokens == 12

    asyncio.run(scenario())


def test_groq_maps_json_mode_and_tool_calls() -> None:
    tool = {
        "type": "function",
        "function": {"name": "lookup", "parameters": {"type": "object"}},
    }
    response_body = body(content="", finish_reason="tool_calls")
    response_body["choices"][0]["message"]["tool_calls"] = [{
        "id": "call-1",
        "type": "function",
        "function": {"name": "lookup", "arguments": '{"symbol":"BTCUSD"}'},
    }]

    async def handler(request_: httpx.Request) -> httpx.Response:
        payload = json.loads(request_.content)
        assert payload["response_format"] == {"type": "json_object"}
        assert payload["tools"] == [tool]
        return httpx.Response(200, json=response_body)

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await GroqProvider(config(), client=client).complete(
                request(tools=(tool,), json_mode=True)
            )
            assert result.tool_calls[0].wire_name == "lookup"
            assert result.tool_calls[0].arguments == {"symbol": "BTCUSD"}

    asyncio.run(scenario())


@pytest.mark.parametrize("status", [400, 500, 502, 503, 504])
def test_groq_http_errors_are_normalized(status: int) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status)

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ProviderUnavailable):
                await GroqProvider(config(), client=client).complete(request())

    asyncio.run(scenario())


@pytest.mark.parametrize("status", [401, 403])
def test_groq_auth_errors_are_normalized(status: int) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status)

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(AuthenticationFailure):
                await GroqProvider(config(), client=client).complete(request())

    asyncio.run(scenario())


def test_groq_rate_limit_and_timeout_are_normalized() -> None:
    async def rate_handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    async def timeout_handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("secret transport detail")

    async def status_timeout_handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(408)

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(rate_handler)) as client:
            with pytest.raises(RateLimitExceeded):
                await GroqProvider(config(), client=client).complete(request())
        async with httpx.AsyncClient(transport=httpx.MockTransport(timeout_handler)) as client:
            with pytest.raises(ProviderTimeout):
                await GroqProvider(config(), client=client).complete(request())
        async with httpx.AsyncClient(transport=httpx.MockTransport(status_timeout_handler)) as client:
            with pytest.raises(ProviderTimeout):
                await GroqProvider(config(), client=client).complete(request())

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "response_body",
    [
        {},
        {"choices": []},
        {"choices": [{"message": {"content": "ok"}, "finish_reason": "unknown"}]},
        {"choices": [{"message": {"content": ""}, "finish_reason": "stop"}]},
    ],
)
def test_groq_parser_fails_closed(response_body: dict[str, object]) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=response_body)

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ProviderUnavailable):
                await GroqProvider(config(), client=client).complete(request())

    asyncio.run(scenario())


def test_groq_telemetry_is_sanitized_and_factory_is_explicit() -> None:
    events: list[tuple[str, dict[str, object]]] = []
    observer = VoiceExecutionObserver(
        None,
        "safe-session",
        sink=lambda event, payload: events.append((event, payload)),
    )

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body())

    async def scenario() -> None:
        metadata = {
            "_voice_observer": observer,
            "voice_session_id": observer.session_id,
            "voice_trace_id": observer.request_id,
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await GroqProvider(config("secret-token"), client=client).complete(
                request(**metadata)
            )

    asyncio.run(scenario())
    rendered = repr(events)
    assert "secret-token" not in rendered
    assert "GROQ_OK" not in rendered
    assert all("prompt" not in payload and "response" not in payload for _, payload in events)

    settings = Settings(
        _env_file=None,
        default_provider="nvidia",
        default_model="nvidia-model",
        nvidia_api_key=SecretStr("nvidia-secret"),
        groq_api_key=SecretStr("groq-secret"),
        groq_model=MODEL,
    )
    factory = ProviderFactory(settings=settings)
    assert isinstance(factory.create(provider=ProviderName.GROQ), GroqProvider)
    assert factory.config.default_provider is ProviderName.NVIDIA
