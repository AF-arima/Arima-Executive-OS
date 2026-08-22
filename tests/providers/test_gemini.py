import asyncio
import json

import httpx
from pydantic import SecretStr
import pytest

from app.core.config import Settings
from app.providers.config import ProviderConfig
from app.providers.exceptions import (
    AuthenticationFailure,
    ProviderTimeout,
    ProviderUnavailable,
    RateLimitExceeded,
)
from app.providers.factory import ProviderFactory
from app.providers.providers.gemini import GeminiProvider
from app.providers.types import (
    CompletionRequest,
    MessageRole,
    ProviderCapabilities,
    ProviderMessage,
    ProviderName,
    ProviderStatus,
)

MODEL = "gemini-low-cost-test-model"


def configuration(*, api_key: str | None = "test-gemini-secret") -> ProviderConfig:
    return ProviderConfig(
        provider=ProviderName.GEMINI,
        default_model=MODEL,
        max_model_tokens=16_000,
        default_temperature=0.2,
        max_output_tokens=1_024,
        api_key=SecretStr(api_key) if api_key is not None else None,
        capabilities=ProviderCapabilities(streaming=True, reasoning=True),
    )


def request() -> CompletionRequest:
    return CompletionRequest(
        model=MODEL,
        messages=(
            ProviderMessage(
                role=MessageRole.SYSTEM,
                content="Follow governed Arima instructions.",
            ),
            ProviderMessage(
                role=MessageRole.USER,
                content="Give the approved read-only response.",
            ),
        ),
        temperature=0.2,
        max_output_tokens=256,
    )


def test_gemini_adapter_uses_server_side_generate_content_contract() -> None:
    async def handler(http_request: httpx.Request) -> httpx.Response:
        assert http_request.url == (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            "gemini-low-cost-test-model:generateContent"
        )
        assert http_request.headers["x-goog-api-key"] == "test-gemini-secret"
        assert "test-gemini-secret" not in str(http_request.url)
        assert json.loads(http_request.content) == {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": "Give the approved read-only response."}
                    ],
                }
            ],
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 256},
            "systemInstruction": {
                "parts": [{"text": "Follow governed Arima instructions."}]
            },
        }
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {
                            "parts": [{"text": "Approved Gemini response."}]
                        },
                        "finishReason": "STOP",
                    }
                ],
                "usageMetadata": {
                    "promptTokenCount": 12,
                    "candidatesTokenCount": 4,
                },
            },
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            provider = GeminiProvider(configuration(), client=client)
            result = await provider.complete(request())
        assert result.content == "Approved Gemini response."
        assert result.finish_reason == "STOP"
        assert result.usage.input_tokens == 12
        assert result.usage.output_tokens == 4
        assert "test-gemini-secret" not in repr(result)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "response_text",
    [
        "Inflation reduces purchasing power and can affect rates and asset prices.",
        "تورم قدرت خرید را کاهش می‌دهد و می‌تواند بر نرخ‌ها و قیمت دارایی‌ها اثر بگذارد.",
    ],
)
def test_gemini_success_preserves_the_provider_response_language(
    response_text: str,
) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "content": {"parts": [{"text": response_text}]},
                        "finishReason": "STOP",
                    }
                ]
            },
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            provider = GeminiProvider(configuration(), client=client)
            result = await provider.complete(request())
        assert result.content == response_text

    asyncio.run(scenario())


def test_gemini_missing_credentials_is_unavailable_and_factory_registers_it() -> None:
    missing = GeminiProvider(configuration(api_key=None))
    health = asyncio.run(missing.health())
    assert health.status is ProviderStatus.UNAVAILABLE
    assert health.available is False

    settings = Settings.model_validate(
        {
            "default_provider": "gemini",
            "default_model": MODEL,
            "gemini_model": MODEL,
            "gemini_api_key": SecretStr("test-gemini-secret"),
        }
    )
    registry = ProviderFactory(settings=settings).build_registry()
    assert tuple(provider.provider for provider in registry.list()) == (
        ProviderName.GEMINI,
    )


def test_gemini_factory_uses_bounded_provider_timeout() -> None:
    settings = Settings.model_validate(
        {
            "default_provider": "gemini",
            "default_model": MODEL,
            "gemini_model": MODEL,
            "gemini_api_key": SecretStr("test-gemini-secret"),
            "ai_provider_timeout_seconds": 15.0,
        }
    )
    provider = ProviderFactory(settings=settings).create()
    assert isinstance(provider, GeminiProvider)
    assert provider._timeout_seconds == 15.0


def test_gemini_http_timeout_is_classified_as_provider_timeout() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("provider timed out")

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            provider = GeminiProvider(configuration(), client=client)
            with pytest.raises(ProviderTimeout):
                await provider.complete(request())

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("status", "exception"),
    [(401, AuthenticationFailure), (429, RateLimitExceeded), (500, ProviderUnavailable)],
)
def test_gemini_failures_are_classified_without_leaking_credentials(
    status: int,
    exception: type[Exception],
) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": "test-gemini-secret"})

    async def scenario() -> None:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ) as client:
            provider = GeminiProvider(configuration(), client=client)
            with pytest.raises(exception) as captured:
                await provider.complete(request())
        assert "test-gemini-secret" not in str(captured.value)

    asyncio.run(scenario())
