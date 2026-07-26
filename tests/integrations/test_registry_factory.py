import pytest

from app.integrations.config import IntegrationPlatformConfig
from app.integrations.connectors import SearchConnector
from app.integrations.exceptions import (
    ConnectorAlreadyRegisteredError,
    ConnectorNotFoundError,
    IntegrationConfigurationError,
)
from app.integrations.factory import ConnectorFactory
from app.integrations.schemas import (
    IntegrationCapability,
    IntegrationProvider,
)


def test_registry_lookup_dimensions_and_catalog_counts() -> None:
    registry = ConnectorFactory().build_registry()
    assert len(registry) == 18
    assert (
        sum(
            len(connector.supported_operations())
            for connector in registry.all()
        )
        == 63
    )
    assert len(registry.find(provider=IntegrationProvider.GOOGLE)) == 4
    assert len(registry.find(operation="read_email")) == 2
    assert len(
        registry.find(
            capabilities=frozenset({IntegrationCapability.MARKET_DATA})
        )
    ) == 2
    assert len(registry.find(version="1.0.0")) == 18
    with pytest.raises(ConnectorNotFoundError):
        registry.get("missing")
    with pytest.raises(ConnectorAlreadyRegisteredError):
        registry.register(registry.get("search"))


def test_factory_supports_replaceable_builders_and_mock_only_config() -> None:
    factory = ConnectorFactory(builders=(SearchConnector,))
    assert len(factory.build_registry()) == 1
    assert isinstance(factory.create("search"), SearchConnector)
    with pytest.raises(IntegrationConfigurationError):
        IntegrationPlatformConfig(allow_real_requests=True)
