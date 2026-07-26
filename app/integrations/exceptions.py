class IntegrationError(Exception):
    """Base exception for the external integration platform."""


class ConnectorNotFoundError(IntegrationError):
    """Raised when a connector cannot be resolved."""


class ConnectorAlreadyRegisteredError(IntegrationError):
    """Raised when a connector name and version are registered twice."""


class IntegrationValidationError(IntegrationError):
    """Raised when a connector request is invalid."""


class IntegrationPermissionDeniedError(IntegrationError):
    """Raised when an integration operation is unauthorized."""


class IntegrationApprovalRequiredError(IntegrationError):
    """Raised when execution requires an approval grant."""


class IntegrationConfigurationError(IntegrationError):
    """Raised when integration platform configuration is invalid."""


class ConnectorUnavailableError(IntegrationError):
    """Raised when a connector is unavailable."""
