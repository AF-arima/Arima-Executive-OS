from __future__ import annotations

from dataclasses import dataclass

from app.integrations.exceptions import IntegrationConfigurationError
from app.integrations.schemas import IntegrationEnvironment


@dataclass(frozen=True, slots=True)
class ConnectorConfig:
    name: str
    enabled: bool = True
    mock: bool = True

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise IntegrationConfigurationError(
                "Connector name is required"
            )
        if not self.mock:
            raise IntegrationConfigurationError(
                "Milestone 4C permits mock connectors only"
            )


@dataclass(frozen=True, slots=True)
class IntegrationPlatformConfig:
    environment: IntegrationEnvironment = IntegrationEnvironment.TEST
    allow_real_requests: bool = False

    def __post_init__(self) -> None:
        if self.allow_real_requests:
            raise IntegrationConfigurationError(
                "Real integration requests are disabled"
            )
