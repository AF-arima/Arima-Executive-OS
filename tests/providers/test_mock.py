import asyncio
from decimal import Decimal

import pytest

from app.core.config import Settings
from app.providers import (
    CompletionRequest,
    EmbeddingRequest,
    InvalidModel,
    MessageRole,
    MockProvider,
    ProviderMessage,
    ProviderName,
    ProviderPlatformConfig,
    ProviderStatus,
)


def mock_provider() -> MockProvider:
    settings = Settings(
        _env_file=None,
        default_provider="mock",
        default_model="mock-model",
    )
    config = ProviderPlatformConfig.from_settings(settings).for_provider(
        ProviderName.MOCK
    )
    return MockProvider(config)


def test_mock_completion_stream_embeddings_and_health_are_deterministic() -> None:
    provider = mock_provider()
    request = CompletionRequest(
        model="mock-model",
        messages=(
            ProviderMessage(
                role=MessageRole.SYSTEM,
                content="Respond deterministically.",
            ),
            ProviderMessage(
                role=MessageRole.USER,
                content="Summarise this plan",
            ),
        ),
        tools=({"name": "projects.read"},),
        json_mode=True,
    )

    async def exercise() -> tuple[object, object, str, object, object]:
        first = await provider.complete(request)
        second = await provider.complete(request)
        chunks = [chunk async for chunk in provider.stream(request)]
        embedding_request = EmbeddingRequest(
            model="mock-model",
            inputs=("alpha", "beta"),
        )
        first_embedding = await provider.embeddings(embedding_request)
        second_embedding = await provider.embeddings(embedding_request)
        return (
            first,
            second,
            "".join(chunk.content for chunk in chunks),
            first_embedding,
            second_embedding,
        )

    first, second, streamed, first_embedding, second_embedding = asyncio.run(
        exercise()
    )
    assert first == second
    assert first.content == "Mock response: Summarise this plan"
    assert streamed == first.content
    assert first.estimated_cost.total_cost == Decimal("0")
    assert first.usage.total_tokens > 0
    assert first_embedding == second_embedding
    assert len(first_embedding.vectors) == 2

    health = asyncio.run(provider.health())
    assert health.status is ProviderStatus.HEALTHY
    assert health.available is True
    assert health.latency_ms == 0


def test_mock_capabilities_token_count_cost_and_invalid_model() -> None:
    provider = mock_provider()
    assert provider.supports_tools("mock-model") is True
    assert provider.supports_streaming("mock-model") is True
    assert provider.supports_images("mock-model") is True
    assert provider.supports_json_mode("mock-model") is True
    assert provider.count_tokens("one two three", model="mock-model") == 3
    information = provider.model_information("mock-model")
    assert information.capabilities.reasoning is True
    assert information.capabilities.embeddings is True
    assert information.pricing.configured is False

    with pytest.raises(InvalidModel):
        provider.model_information("unknown-model")
