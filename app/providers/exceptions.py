from app.services.exceptions import ServiceError


class ProviderError(ServiceError):
    """Base class for provider-platform failures."""

    def __init__(
        self,
        message: str = "",
        *,
        status_code: int | None = None,
        safe_failure_category: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.safe_failure_category = safe_failure_category


class ProviderUnavailable(ProviderError):
    pass


class InvalidModel(ProviderError):
    pass


class AuthenticationFailure(ProviderError):
    pass


class RateLimitExceeded(ProviderError):
    pass


class ProviderTimeout(ProviderError):
    pass


class ProviderConfigurationError(ProviderError):
    pass
