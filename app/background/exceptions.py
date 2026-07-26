class BackgroundError(Exception):
    """Base exception for background intelligence."""


class BackgroundJobNotFoundError(BackgroundError):
    pass


class BackgroundConfigurationError(BackgroundError):
    pass


class BackgroundValidationError(BackgroundError):
    pass


class BackgroundPermissionDeniedError(BackgroundError):
    pass


class BackgroundApprovalRequiredError(BackgroundError):
    pass


class BackgroundCancellationError(BackgroundError):
    pass


class BackgroundTimeoutError(BackgroundError):
    pass


class BackgroundRetryExhaustedError(BackgroundError):
    pass
