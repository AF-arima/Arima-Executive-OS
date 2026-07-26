from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import cast

from app.integrations.base import IntegrationConnector
from app.integrations.config import IntegrationPlatformConfig
from app.integrations.connectors import MOCK_CONNECTOR_TYPES
from app.integrations.exceptions import IntegrationConfigurationError
from app.integrations.registry import ConnectorRegistry

ConnectorBuilder = Callable[[], IntegrationConnector]


class ConnectorFactory:
    def __init__(
        self,
        *,
        config: IntegrationPlatformConfig | None = None,
        builders: Iterable[ConnectorBuilder] | None = None,
        registry: ConnectorRegistry | None = None,
    ) -> None:
        self.config = config or IntegrationPlatformConfig()
        selected = builders or cast(
            tuple[ConnectorBuilder, ...], MOCK_CONNECTOR_TYPES
        )
        self.builders: tuple[ConnectorBuilder, ...] = tuple(selected)
        if not self.builders:
            raise IntegrationConfigurationError(
                "At least one connector builder is required"
            )
        self.registry = registry or ConnectorRegistry()

    def create_all(self) -> tuple[IntegrationConnector, ...]:
        return tuple(builder() for builder in self.builders)

    def build_registry(self) -> ConnectorRegistry:
        for connector in self.create_all():
            self.registry.register(connector)
        return self.registry

    def create(
        self, connector: str, version: str | None = None
    ) -> IntegrationConnector:
        registry = ConnectorRegistry(self.create_all())
        return registry.get(connector, version)
