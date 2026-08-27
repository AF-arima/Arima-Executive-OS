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


@pytest.mark.parametrize("status", [400, 404, 500, 502, 503, 504])
def test_groq_http_errors_are_normalized(status: int) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status)

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ProviderUnavailable) as caught:
                await GroqProvider(config(), client=client).complete(request())
            assert caught.value.status_code == status
            assert caught.value.safe_failure_category == (
                "bad_request"
                if status == 400
                else "not_found"
                if status == 404
                else "server_error"
            )

    asyncio.run(scenario())


@pytest.mark.parametrize("status", [401, 403])
def test_groq_auth_errors_are_normalized(status: int) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status)

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(AuthenticationFailure) as caught:
                await GroqProvider(config(), client=client).complete(request())
            assert caught.value.status_code == status
            assert caught.value.safe_failure_category == (
                "unauthorized" if status == 401 else "forbidden"
            )

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
            with pytest.raises(RateLimitExceeded) as caught:
                await GroqProvider(config(), client=client).complete(request())
            assert caught.value.status_code == 429
            assert caught.value.safe_failure_category == "rate_limited"
        async with httpx.AsyncClient(transport=httpx.MockTransport(timeout_handler)) as client:
            with pytest.raises(ProviderTimeout) as caught:
                await GroqProvider(config(), client=client).complete(request())
            assert caught.value.status_code is None
            assert caught.value.safe_failure_category == "timeout"
        async with httpx.AsyncClient(transport=httpx.MockTransport(status_timeout_handler)) as client:
            with pytest.raises(ProviderTimeout) as caught:
                await GroqProvider(config(), client=client).complete(request())
            assert caught.value.status_code == 408
            assert caught.value.safe_failure_category == "timeout"

    asyncio.run(scenario())


def test_groq_transport_error_preserves_safe_category() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("secret transport detail")

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ProviderUnavailable) as caught:
                await GroqProvider(config(), client=client).complete(request())
            assert caught.value.status_code is None
            assert caught.value.safe_failure_category == "transport_error"

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
            with pytest.raises(ProviderUnavailable) as caught:
                await GroqProvider(config(), client=client).complete(request())
            assert caught.value.status_code is None
            assert caught.value.safe_failure_category == "parser_error"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("response_body", "stage", "detail"),
    [
        ({}, "choices_missing", "missing_field"),
        ({"choices": "invalid"}, "choices_missing", "wrong_type"),
        ({"choices": []}, "choices_empty", "empty_value"),
        ({"choices": ["invalid"]}, "choices_missing", "malformed_structure"),
        ({"choices": [{}]}, "message_invalid", "missing_field"),
        ({"choices": [{"message": "invalid"}]}, "message_invalid", "wrong_type"),
        ({"choices": [{"message": {"content": 1}}]}, "content_invalid", "wrong_type"),
        ({"choices": [{"message": {"content": ""}, "finish_reason": "stop"}]}, "content_empty", "empty_value"),
        ({"choices": [{"message": {"content": "ok", "tool_calls": "invalid"}, "finish_reason": "stop"}]}, "tool_calls_invalid", "wrong_type"),
        ({"choices": [{"message": {"content": "ok", "tool_calls": [{"id": 1}]}, "finish_reason": "stop"}]}, "tool_calls_invalid", "malformed_structure"),
        ({"choices": [{"message": {"content": "ok", "tool_calls": [{"id": "id", "function": "invalid"}]}, "finish_reason": "stop"}]}, "tool_calls_invalid", "malformed_structure"),
        ({"choices": [{"message": {"content": "ok", "tool_calls": [{"id": "id", "function": {"name": "fn", "arguments": 1}}]}, "finish_reason": "stop"}]}, "tool_calls_invalid", "wrong_type"),
        ({"choices": [{"message": {"content": "ok", "tool_calls": [{"id": "id", "function": {"name": "fn", "arguments": "["}}]}, "finish_reason": "stop"}]}, "tool_calls_invalid", "malformed_structure"),
        ({"choices": [{"message": {"content": "ok", "tool_calls": [{"id": "id", "function": {"name": "fn", "arguments": "[]"}}]}, "finish_reason": "stop"}]}, "tool_calls_invalid", "wrong_type"),
        ({"choices": [{"message": {"content": "ok"}, "finish_reason": "invalid"}]}, "finish_reason_invalid", "unsupported_value"),
        ({"choices": [{"message": {"content": "", "tool_calls": []}, "finish_reason": "tool_calls"}]}, "tool_calls_invalid", "empty_value"),
    ],
)
def test_groq_completion_parser_reports_safe_failure_stage(
    response_body: dict[str, object], stage: str, detail: str
) -> None:
    with pytest.raises(ProviderUnavailable) as caught:
        GroqProvider._completion(response_body)
    error = caught.value
    assert error.parser_failure_stage == stage
    assert error.parser_failure_detail == detail


def test_groq_body_parser_reports_decode_and_response_object_stages() -> None:
    with pytest.raises(ProviderUnavailable) as invalid_json:
        GroqProvider._response_body(httpx.Response(200, content=b"{"))
    assert invalid_json.value.parser_failure_stage == "json_decode"
    assert invalid_json.value.parser_failure_detail == "exception"

    with pytest.raises(ProviderUnavailable) as invalid_object:
        GroqProvider._response_body(httpx.Response(200, json=[]))
    assert invalid_object.value.parser_failure_stage == "response_object"
    assert invalid_object.value.parser_failure_detail == "wrong_type"


def test_groq_unexpected_parser_exception_reports_unknown() -> None:
    class UnexpectedResponse:
        status_code = 200

        def json(self) -> object:
            raise RuntimeError("secret response detail")

    class FakeClient:
        async def post(self, *args: object, **kwargs: object) -> UnexpectedResponse:
            del args, kwargs
            return UnexpectedResponse()

    async def scenario() -> None:
        with pytest.raises(RuntimeError) as caught:
            await GroqProvider(config(), client=FakeClient()).complete(request())
        assert caught.value.parser_failure_stage == "unknown"
        assert caught.value.parser_failure_detail == "exception"

    asyncio.run(scenario())


def test_groq_usage_parser_reports_safe_stage() -> None:
    with pytest.raises(ProviderUnavailable) as caught:
        GroqProvider._usage({"usage": {"prompt_tokens": "secret", "completion_tokens": 1}})
    assert caught.value.parser_failure_stage == "usage_invalid"
    assert caught.value.parser_failure_detail == "wrong_type"
    assert "secret" not in repr(caught.value)


def test_groq_parser_stage_is_allowlisted_in_failure_telemetry() -> None:
    events: list[tuple[str, dict[str, object]]] = []
    observer = VoiceExecutionObserver(
        None,
        "safe-session",
        sink=lambda event, payload: events.append((event, payload)),
    )

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "secret completion"}, "finish_reason": "UNSAFE_FINISH_REASON"}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    async def scenario() -> None:
        metadata = {
            "_voice_observer": observer,
            "voice_session_id": observer.session_id,
            "voice_trace_id": observer.request_id,
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ProviderUnavailable) as caught:
                await GroqProvider(config(), client=client).complete(request(**metadata))
            assert caught.value.parser_failure_stage == "finish_reason_invalid"

    asyncio.run(scenario())
    failure = events[-1][1]
    assert failure["parser_failure_stage"] == "finish_reason_invalid"
    assert failure["parser_failure_detail"] == "unsupported_value"
    rendered = repr(events)
    assert "secret completion" not in rendered
    assert "UNSAFE_FINISH_REASON" not in rendered


@pytest.mark.parametrize(
    "response_body",
    [
        body(),
        {
            "choices": [{
                "message": {
                    "content": "",
                    "reasoning": "PRIVATE_REASONING_SENTINEL",
                },
                "finish_reason": "length",
            }],
            "usage": {},
        },
        {
            "choices": [{"message": {"content": None}, "finish_reason": "length"}],
        },
        {
            "choices": [{
                "message": {
                    "content": "",
                    "tool_calls": [{
                        "id": "PRIVATE_TOOL_ID_SENTINEL",
                        "function": {"name": "lookup", "arguments": "PRIVATE_ARGS_SENTINEL"},
                    }],
                },
                "finish_reason": "tool_calls",
            }],
        },
        {
            "choices": [{
                "message": {
                    "content": "PRIVATE_CONTENT_SENTINEL",
                    "tool_calls": [{"id": "call", "function": {"name": "lookup", "arguments": "{}"}}],
                },
                "finish_reason": "tool_calls",
            }],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        },
    ],
)
def test_groq_response_shape_contains_only_structural_metadata(
    response_body: dict[str, object],
) -> None:
    shape = GroqProvider._response_shape(response_body)
    assert set(shape) == {
        "choices_count", "message_type", "content_type", "content_empty",
        "reasoning_present", "reasoning_type", "tool_calls_present",
        "tool_calls_count", "finish_reason", "usage_present",
    }
    rendered = repr(shape)
    assert all(
        sentinel not in rendered
        for sentinel in (
            "PRIVATE_REASONING_SENTINEL",
            "PRIVATE_TOOL_ID_SENTINEL",
            "PRIVATE_ARGS_SENTINEL",
            "PRIVATE_CONTENT_SENTINEL",
        )
    )


def test_groq_normalization_failure_reports_safe_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, dict[str, object]]] = []
    observer = VoiceExecutionObserver(
        None,
        "safe-session",
        sink=lambda event, payload: events.append((event, payload)),
    )
    def fail(*args: object, **kwargs: object) -> float:
        del args, kwargs
        raise RuntimeError("secret normalization detail")

    monkeypatch.setattr(GroqProvider, "estimate_cost", fail)

    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body())

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            metadata = {
                "_voice_observer": observer,
                "voice_session_id": observer.session_id,
                "voice_trace_id": observer.request_id,
            }
            with pytest.raises(RuntimeError):
                await GroqProvider(config(), client=client).complete(request(**metadata))

    asyncio.run(scenario())
    failure = events[-1][1]
    assert failure["parser_failure_stage"] == "normalization"
    assert failure["parser_failure_detail"] == "exception"
    assert "secret normalization detail" not in repr(events)


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
