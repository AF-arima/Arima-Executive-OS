from __future__ import annotations

from collections.abc import Iterable

from app.integrations.base import IntegrationConnector
from app.integrations.exceptions import (
    ConnectorAlreadyRegisteredError,
    ConnectorNotFoundError,
    IntegrationConfigurationError,
)
from app.integrations.schemas import (
    IntegrationCapability,
    IntegrationProvider,
)


class ConnectorRegistry:
    def __init__(
        self, connectors: Iterable[IntegrationConnector] = ()
    ) -> None:
        self._connectors: dict[
            tuple[str, str], IntegrationConnector
        ] = {}
        for connector in connectors:
            self.register(connector)

    def register(self, connector: IntegrationConnector) -> None:
        key = (
            connector.connector_name(),
            connector.connector_version(),
        )
        if key in self._connectors:
            raise ConnectorAlreadyRegisteredError(
                f"Connector already registered: {key[0]}@{key[1]}"
            )
        metadata = connector.metadata()
        if (
            metadata.name != connector.connector_name()
            or metadata.version != connector.connector_version()
            or metadata.provider is not connector.provider()
        ):
            raise IntegrationConfigurationError(
                "Connector metadata does not match connector identity"
            )
        operation_names = [
            operation.name for operation in connector.supported_operations()
        ]
        if not operation_names or len(operation_names) != len(
            set(operation_names)
        ):
            raise IntegrationConfigurationError(
                "Connector operations must be non-empty and unique"
            )
        self._connectors[key] = connector

    def get(
        self, connector: str, version: str | None = None
    ) -> IntegrationConnector:
        matches = [
            item
            for (name, item_version), item in self._connectors.items()
            if name == connector
            and (version is None or version == item_version)
        ]
        if not matches:
            suffix = f"@{version}" if version else ""
            raise ConnectorNotFoundError(
                f"Connector not registered: {connector}{suffix}"
            )
        return max(matches, key=lambda item: item.connector_version())

    def find(
        self,
        *,
        provider: IntegrationProvider | None = None,
        connector: str | None = None,
        operation: str | None = None,
        capabilities: frozenset[IntegrationCapability] = frozenset(),
        version: str | None = None,
    ) -> tuple[IntegrationConnector, ...]:
        matches = []
        for item in self._connectors.values():
            if provider is not None and item.provider() is not provider:
                continue
            if connector is not None and item.connector_name() != connector:
                continue
            if version is not None and item.connector_version() != version:
                continue
            if operation is not None and operation not in {
                candidate.name for candidate in item.supported_operations()
            }:
                continue
            if not capabilities.issubset(item.capabilities()):
                continue
            matches.append(item)
        return tuple(
            sorted(matches, key=lambda item: item.connector_name())
        )

    def all(self) -> tuple[IntegrationConnector, ...]:
        return tuple(
            sorted(
                self._connectors.values(),
                key=lambda item: item.connector_name(),
            )
        )

    def __len__(self) -> int:
        return len(self._connectors)
