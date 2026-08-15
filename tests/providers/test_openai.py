import asyncio
import json

import httpx
from pydantic import SecretStr
import pytest

from app.providers.config import ProviderConfig
from app.providers.exceptions import (
    AuthenticationFailure,
    ProviderUnavailable,
)
from app.providers.providers.openai import OpenAIProvider
from app.providers.types import (
    CompletionRequest,
    MessageRole,
    ProviderCapabilities,
    ProviderMessage,
    ProviderName,
)


def configuration() -> ProviderConfig:
    return ProviderConfig(
        provider=ProviderName.OPENAI,
        default_model="approved-openai-model",
        max_model_tokens=16_000,
        default_temperature=0.2,
        max_output_tokens=1_024,
        api_key=SecretStr("test-server-secret"),
        capabilities=ProviderCapabilities(
            streaming=True,
            reasoning=True,
        ),
    )


def request() -> CompletionRequest:
    return CompletionRequest(
        model="approved-openai-model",
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
        max_output_tokens=256,
    )


def test_openai_responses_adapter_is_server_side_and_non_storing() -> None:
    async def handler(http_request: httpx.Request) -> httpx.Response:
        assert http_request.url == "https://api.openai.com/v1/responses"
        assert http_request.headers["Authorization"] == (
            "Bearer test-server-secret"
        )
        payload = json.loads(http_request.content)
        assert payload == {
            "model": "approved-openai-model",
            "input": [
                {
                    "role": "system",
                    "content": "Follow the governed Arima instructions.",
                },
                {
                    "role": "user",
                    "content": "Give the approved voice response.",
                },
            ],
            "max_output_tokens": 256,
            "store": False,
        }
        return httpx.Response(
            200,
            json={
                "id": "resp_test",
                "model": "approved-openai-model",
                "status": "completed",
                "output": [
                    {"type": "reasoning", "content": []},
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "Approved voice response.",
                            }
                        ],
                    },
                ],
                "usage": {"input_tokens": 12, "output_tokens": 4},
            },
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            provider = OpenAIProvider(configuration(), client=client)
            result = await provider.complete(request())

        assert result.content == "Approved voice response."
        assert result.usage.input_tokens == 12
        assert result.usage.output_tokens == 4
        assert result.metadata == {
            "response_id": "resp_test",
            "latency_ms": result.metadata["latency_ms"],
            "stored_by_provider": False,
        }
        assert "test-server-secret" not in repr(result)

    asyncio.run(scenario())


@pytest.mark.parametrize("status", [401, 403])
def test_openai_authentication_failures_are_redacted(status: int) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status,
            json={"error": {"message": "test-server-secret leaked"}},
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            provider = OpenAIProvider(configuration(), client=client)
            with pytest.raises(AuthenticationFailure) as captured:
                await provider.complete(request())
        assert "test-server-secret" not in str(captured.value)

    asyncio.run(scenario())


def test_openai_invalid_success_response_fails_closed() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"status": "completed", "output": [], "usage": {}},
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            provider = OpenAIProvider(configuration(), client=client)
            with pytest.raises(ProviderUnavailable, match="no usable output"):
                await provider.complete(request())

    asyncio.run(scenario())


def test_openai_incomplete_response_fails_closed() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "status": "incomplete",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {"type": "output_text", "text": "Partial"}
                        ],
                    }
                ],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            provider = OpenAIProvider(configuration(), client=client)
            with pytest.raises(ProviderUnavailable, match="did not complete"):
                await provider.complete(request())

    asyncio.run(scenario())
