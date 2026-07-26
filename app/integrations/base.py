from __future__ import annotations

from abc import ABC, abstractmethod
from time import perf_counter
from typing import Any

from app.integrations.context import IntegrationExecutionContext
from app.integrations.exceptions import IntegrationValidationError
from app.integrations.health import ConnectorHealthMonitor
from app.integrations.schemas import (
    ConnectorHealth,
    ConnectorMetadata,
    ConnectorOperation,
    IntegrationCapability,
    IntegrationPermission,
    IntegrationProvider,
    ValidatedIntegrationRequest,
)


class IntegrationConnector(ABC):
    @abstractmethod
    def connector_name(self) -> str: ...

    @abstractmethod
    def connector_version(self) -> str: ...

    @abstractmethod
    def connector_description(self) -> str: ...

    @abstractmethod
    def provider(self) -> IntegrationProvider: ...

    @abstractmethod
    def supported_operations(self) -> tuple[ConnectorOperation, ...]: ...

    @abstractmethod
    def required_permissions(
        self, operation: str
    ) -> frozenset[IntegrationPermission]: ...

    @abstractmethod
    async def health(self) -> ConnectorHealth: ...

    @abstractmethod
    def metadata(self) -> ConnectorMetadata: ...

    @abstractmethod
    def validate_request(
        self, operation: str, payload: dict[str, Any]
    ) -> ValidatedIntegrationRequest: ...

    @abstractmethod
    async def execute(
        self,
        request: ValidatedIntegrationRequest,
        context: IntegrationExecutionContext,
    ) -> dict[str, Any]: ...

    @abstractmethod
    async def dry_run(
        self,
        request: ValidatedIntegrationRequest,
        context: IntegrationExecutionContext,
    ) -> dict[str, Any]: ...

    @abstractmethod
    def capabilities(self) -> frozenset[IntegrationCapability]: ...


class DeterministicMockConnector(IntegrationConnector):
    name: str
    version = "1.0.0"
    description: str
    provider_name: IntegrationProvider
    operations: tuple[ConnectorOperation, ...]
    connector_capabilities: frozenset[IntegrationCapability]

    def __init__(self) -> None:
        self._health = ConnectorHealthMonitor()

    def connector_name(self) -> str:
        return self.name

    def connector_version(self) -> str:
        return self.version

    def connector_description(self) -> str:
        return self.description

    def provider(self) -> IntegrationProvider:
        return self.provider_name

    def supported_operations(self) -> tuple[ConnectorOperation, ...]:
        return self.operations

    def required_permissions(
        self, operation: str
    ) -> frozenset[IntegrationPermission]:
        return self._operation(operation).permissions

    async def health(self) -> ConnectorHealth:
        return self._health.snapshot()

    def metadata(self) -> ConnectorMetadata:
        return ConnectorMetadata(
            name=self.connector_name(),
            version=self.connector_version(),
            description=self.connector_description(),
            provider=self.provider(),
            operations=self.supported_operations(),
            capabilities=self.capabilities(),
        )

    def validate_request(
        self, operation: str, payload: dict[str, Any]
    ) -> ValidatedIntegrationRequest:
        if not isinstance(payload, dict):
            raise IntegrationValidationError(
                "Connector payload must be an object"
            )
        return ValidatedIntegrationRequest(
            operation=self._operation(operation),
            payload=payload,
        )

    async def execute(
        self,
        request: ValidatedIntegrationRequest,
        context: IntegrationExecutionContext,
    ) -> dict[str, Any]:
        started = perf_counter()
        try:
            response = self._response(request, context, dry_run=False)
        except Exception:
            self._health.record_failure(
                (perf_counter() - started) * 1000
            )
            raise
        self._health.record_success((perf_counter() - started) * 1000)
        return response

    async def dry_run(
        self,
        request: ValidatedIntegrationRequest,
        context: IntegrationExecutionContext,
    ) -> dict[str, Any]:
        return self._response(request, context, dry_run=True)

    def capabilities(self) -> frozenset[IntegrationCapability]:
        return self.connector_capabilities

    def _operation(self, operation: str) -> ConnectorOperation:
        for candidate in self.operations:
            if candidate.name == operation:
                return candidate
        raise IntegrationValidationError(
            f"Unsupported operation {operation!r} for {self.name}"
        )

    def _response(
        self,
        request: ValidatedIntegrationRequest,
        context: IntegrationExecutionContext,
        *,
        dry_run: bool,
    ) -> dict[str, Any]:
        return {
            "mock": True,
            "dry_run": dry_run,
            "connector": self.connector_name(),
            "provider": self.provider().value,
            "operation": request.operation.name,
            "request": request.payload,
            "items": [],
            "environment": context.environment.value,
        }
