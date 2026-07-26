from uuid import UUID

from app.services.exceptions import ServiceError


class ExecutionError(ServiceError):
    """Base class for expected execution-platform failures."""


class ProviderUnavailable(ExecutionError):
    pass


class ProviderFailure(ExecutionError):
    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class ToolFailure(ExecutionError):
    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class ApprovalRequired(ExecutionError):
    def __init__(
        self,
        tool_slug: str,
        *,
        execution_id: UUID,
        approval_id: UUID,
    ) -> None:
        super().__init__(f"Approval required for {tool_slug}")
        self.tool_slug = tool_slug
        self.execution_id = execution_id
        self.approval_id = approval_id


class ExecutionCancelled(ExecutionError):
    pass


class ExecutionTimeout(ExecutionError):
    pass


class InvalidTransition(ExecutionError):
    pass


class ContextBuildFailure(ExecutionError):
    pass


class PromptBuildFailure(ExecutionError):
    pass


class RetryExhausted(ExecutionError):
    def __init__(self, attempts: int, last_error: Exception) -> None:
        super().__init__(f"Retry attempts exhausted after {attempts} attempts")
        self.attempts = attempts
        self.last_error = last_error
