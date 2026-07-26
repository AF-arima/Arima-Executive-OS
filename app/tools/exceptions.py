class ToolError(Exception):
    """Base exception for the internal tool framework."""


class ToolNotFoundError(ToolError):
    """Raised when a requested tool is not registered."""


class ToolAlreadyRegisteredError(ToolError):
    """Raised when a tool name and version are registered twice."""


class ToolValidationError(ToolError):
    """Raised when tool input is invalid."""


class ToolPermissionDeniedError(ToolError):
    """Raised when an execution context cannot invoke a tool."""


class ToolExecutionError(ToolError):
    """Raised when an internal capability cannot complete."""


class ToolConfigurationError(ToolError):
    """Raised when the registry or factory is misconfigured."""
