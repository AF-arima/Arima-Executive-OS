import asyncio

import pytest

from app.core.config import Settings
from app.providers import (
    InvalidModel,
    MockProvider,
    OpenAIProvider,
    ProviderCapability,
    ProviderConfigurationError,
    ProviderFactory,
    ProviderName,
    ProviderRegistry,
    ProviderStatus,
    ProviderUnavailable,
)


def provider_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "_env_file": None,
        "default_provider": "mock",
        "default_model": "mock-model",
    }
    values.update(overrides)
    return Settings(**values)


def test_registry_dynamic_registration_and_capability_lookup() -> None:
    factory = ProviderFactory(settings=provider_settings())
    mock = factory.create()
    registry = ProviderRegistry()
    registry.register(mock)
    assert registry.get("mock", model="mock-model") is mock
    assert registry.resolve(model="mock-model") is mock
    assert registry.resolve(
        capabilities=frozenset(
            {
                ProviderCapability.STREAMING,
                ProviderCapability.EMBEDDINGS,
                ProviderCapability.TOOLS,
            }
        )
    ) is mock

    with pytest.raises(ProviderConfigurationError):
        registry.register(mock)
    with pytest.raises(InvalidModel):
        registry.get(ProviderName.MOCK, model="not-configured")

    registry.unregister("mock")
    with pytest.raises(ProviderUnavailable):
        registry.get("mock")


def test_factory_defaults_explicit_stubs_registry_and_model_validation() -> None:
    factory = ProviderFactory(settings=provider_settings())
    assert isinstance(factory.create(), MockProvider)
    openai = factory.create(provider=ProviderName.OPENAI)
    assert isinstance(openai, OpenAIProvider)
    health = asyncio.run(openai.health())
    assert health.status is ProviderStatus.UNAVAILABLE
    assert health.available is False

    registry = factory.build_registry(tuple(ProviderName))
    assert len(registry.list()) == len(ProviderName)
    matches = registry.find(
        capabilities=frozenset({ProviderCapability.REASONING})
    )
    assert [provider.provider for provider in matches] == [
        ProviderName.MOCK,
        ProviderName.NVIDIA,
        ProviderName.OPENAI,
    ]

    with pytest.raises(InvalidModel):
        factory.create(provider="mock", model="unknown")
