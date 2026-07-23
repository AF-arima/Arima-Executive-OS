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


class UserNotFoundError(AuthenticationError):
    pass


class RoleNotFoundError(AuthenticationError):
    pass
