class OrchestrationError(Exception):
    """Base orchestration exception."""


class IntentDetectionError(OrchestrationError):
    pass


class RoutingError(OrchestrationError):
    pass


class PlanningError(OrchestrationError):
    pass


class OrchestrationApprovalRequired(OrchestrationError):
    pass


class OrchestrationExecutionError(OrchestrationError):
    pass


class OrchestrationBudgetExceeded(OrchestrationError):
    pass


class OrchestrationFallbackExhausted(OrchestrationError):
    pass


class OrchestrationConfigurationError(OrchestrationError):
    pass
