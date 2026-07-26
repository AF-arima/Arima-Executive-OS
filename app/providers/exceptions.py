from app.services.exceptions import ServiceError


class ProviderError(ServiceError):
    """Base class for provider-platform failures."""


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
