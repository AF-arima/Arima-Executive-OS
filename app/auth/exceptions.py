class AuthenticationError(Exception):
    """Base class for expected authentication failures."""


class DuplicateEmailError(AuthenticationError):
    pass


class InvalidCredentialsError(AuthenticationError):
    pass


class InactiveUserError(AuthenticationError):
    pass


class InvalidTokenError(AuthenticationError):
    pass


class TokenReuseError(InvalidTokenError):
    pass


class InvalidSecurityTokenError(AuthenticationError):
    pass


class EmailNotVerifiedError(AuthenticationError):
    pass


class AccountLockedError(AuthenticationError):
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("Account temporarily locked")
        self.retry_after_seconds = retry_after_seconds


class RateLimitExceededError(AuthenticationError):
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("Too many requests")
        self.retry_after_seconds = retry_after_seconds


class EmailDeliveryError(AuthenticationError):
    pass


class CsrfValidationError(AuthenticationError):
    pass


class MFARequiredError(AuthenticationError):
    pass


class MFAAlreadyEnabledError(AuthenticationError):
    pass


class InvalidMFACodeError(AuthenticationError):
    pass


class MFALockedError(AuthenticationError):
    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__("MFA temporarily locked")
        self.retry_after_seconds = retry_after_seconds


class UserNotFoundError(AuthenticationError):
    pass


class RoleNotFoundError(AuthenticationError):
    pass
