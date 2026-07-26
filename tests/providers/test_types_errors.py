from datetime import datetime
from decimal import Decimal

import pytest

from app.providers import (
    AuthenticationFailure,
    EstimatedCost,
    InvalidModel,
    ModelInfo,
    PricingInfo,
    ProviderCapabilities,
    ProviderCapability,
    ProviderConfigurationError,
    ProviderError,
    ProviderHealth,
    ProviderName,
    ProviderStatus,
    ProviderTimeout,
    ProviderUnavailable,
    RateLimitExceeded,
    TokenUsage,
)
from app.services.exceptions import ServiceError


def test_capability_cost_usage_model_and_health_contracts() -> None:
    capabilities = ProviderCapabilities(
        streaming=True,
        tools=True,
        embeddings=True,
    )
    assert capabilities.enabled() == frozenset(
        {
            ProviderCapability.STREAMING,
            ProviderCapability.TOOLS,
            ProviderCapability.EMBEDDINGS,
        }
    )
    assert capabilities.supports(
        frozenset({ProviderCapability.STREAMING})
    )

    usage = TokenUsage(input_tokens=10, output_tokens=5)
    assert usage.total_tokens == 15
    cost = EstimatedCost(
        input_cost=Decimal("0.10"),
        output_cost=Decimal("0.20"),
        total_cost=Decimal("0.30"),
    )
    assert cost.currency == "GBP"
    model = ModelInfo(
        provider=ProviderName.MOCK,
        model="mock-model",
        display_name="Mock",
        context_window=1_000,
        max_output_tokens=100,
        capabilities=capabilities,
        pricing=PricingInfo(),
    )
    assert model.pricing.configured is False

    health = ProviderHealth(
        provider=ProviderName.MOCK,
        status=ProviderStatus.UNKNOWN,
        available=False,
        latency_ms=None,
    )
    assert health.checked_at.tzinfo is not None

    with pytest.raises(ValueError):
        TokenUsage(input_tokens=-1, output_tokens=0)
    with pytest.raises(ValueError):
        EstimatedCost(
            input_cost=Decimal("0.10"),
            output_cost=Decimal("0.20"),
            total_cost=Decimal("0.40"),
        )
    with pytest.raises(ValueError):
        ProviderHealth(
            provider=ProviderName.MOCK,
            status=ProviderStatus.HEALTHY,
            available=True,
            latency_ms=0,
            checked_at=datetime(2026, 1, 1),
        )


def test_provider_errors_reuse_service_error_hierarchy() -> None:
    assert issubclass(ProviderError, ServiceError)
    for error_type in (
        ProviderUnavailable,
        InvalidModel,
        AuthenticationFailure,
        RateLimitExceeded,
        ProviderTimeout,
        ProviderConfigurationError,
    ):
        assert issubclass(error_type, ProviderError)
