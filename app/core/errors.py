import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.auth.exceptions import (
    AccountLockedError,
    CsrfValidationError,
    DuplicateEmailError,
    EmailDeliveryError,
    EmailNotVerifiedError,
    InactiveUserError,
    InvalidCredentialsError,
    InvalidSecurityTokenError,
    InvalidTokenError,
    RateLimitExceededError,
    RoleNotFoundError,
    UserNotFoundError,
)
from app.services.exceptions import (
    InvalidAnalyticsRequestError,
    PermissionDeniedError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from app.orchestration.exceptions import RoutingError

BEARER_HEADERS = {"WWW-Authenticate": "Bearer"}
logger = logging.getLogger("arima.email")


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(
        DuplicateEmailError,
        _duplicate_email_handler,
    )
    app.add_exception_handler(
        InvalidCredentialsError,
        _invalid_credentials_handler,
    )
    app.add_exception_handler(InactiveUserError, _inactive_user_handler)
    app.add_exception_handler(InvalidTokenError, _invalid_token_handler)
    app.add_exception_handler(
        InvalidSecurityTokenError,
        _invalid_security_token_handler,
    )
    app.add_exception_handler(
        EmailNotVerifiedError,
        _email_not_verified_handler,
    )
    app.add_exception_handler(AccountLockedError, _account_locked_handler)
    app.add_exception_handler(
        RateLimitExceededError,
        _rate_limit_exceeded_handler,
    )
    app.add_exception_handler(EmailDeliveryError, _email_delivery_handler)
    app.add_exception_handler(CsrfValidationError, _csrf_validation_handler)
    app.add_exception_handler(UserNotFoundError, _user_not_found_handler)
    app.add_exception_handler(RoleNotFoundError, _role_not_found_handler)
    app.add_exception_handler(
        ResourceNotFoundError,
        _resource_not_found_handler,
    )
    app.add_exception_handler(
        ResourceConflictError,
        _resource_conflict_handler,
    )
    app.add_exception_handler(
        PermissionDeniedError,
        _permission_denied_handler,
    )
    app.add_exception_handler(
        InvalidAnalyticsRequestError,
        _invalid_analytics_request_handler,
    )
    app.add_exception_handler(RoutingError, _routing_error_handler)
    app.add_exception_handler(
        RequestValidationError,
        _validation_error_handler,
    )


async def _duplicate_email_handler(
    request: Request,
    error: Exception,
) -> JSONResponse:
    return _error_response(status.HTTP_409_CONFLICT, "Email is already registered")


async def _invalid_credentials_handler(
    request: Request,
    error: Exception,
) -> JSONResponse:
    return _error_response(
        status.HTTP_401_UNAUTHORIZED,
        "Invalid email or password",
        BEARER_HEADERS,
    )


async def _inactive_user_handler(
    request: Request,
    error: Exception,
) -> JSONResponse:
    return _error_response(status.HTTP_403_FORBIDDEN, "Inactive user")


async def _invalid_token_handler(
    request: Request,
    error: Exception,
) -> JSONResponse:
    return _error_response(
        status.HTTP_401_UNAUTHORIZED,
        "Invalid or expired token",
        BEARER_HEADERS,
    )


async def _invalid_security_token_handler(
    request: Request,
    error: Exception,
) -> JSONResponse:
    return _error_response(
        status.HTTP_400_BAD_REQUEST,
        "Invalid or expired security token",
    )


async def _email_not_verified_handler(
    request: Request,
    error: Exception,
) -> JSONResponse:
    return _error_response(
        status.HTTP_403_FORBIDDEN,
        "Email verification is required",
    )


async def _account_locked_handler(
    request: Request,
    error: Exception,
) -> JSONResponse:
    if not isinstance(error, AccountLockedError):
        raise error
    return _error_response(
        status.HTTP_423_LOCKED,
        "Account temporarily locked. Try again later.",
        {"Retry-After": str(error.retry_after_seconds)},
    )


async def _rate_limit_exceeded_handler(
    request: Request,
    error: Exception,
) -> JSONResponse:
    if not isinstance(error, RateLimitExceededError):
        raise error
    return _error_response(
        status.HTTP_429_TOO_MANY_REQUESTS,
        "Too many requests. Try again later.",
        {"Retry-After": str(error.retry_after_seconds)},
    )


async def _email_delivery_handler(
    request: Request,
    error: Exception,
) -> JSONResponse:
    logger.error(
        "email_delivery_failed",
        exc_info=error,
        extra={
            "correlation_id": getattr(request.state, "correlation_id", None),
            "method": request.method,
            "path": request.url.path,
        },
    )
    return _error_response(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "Email delivery is temporarily unavailable",
    )


async def _csrf_validation_handler(
    request: Request,
    error: Exception,
) -> JSONResponse:
    return _error_response(
        status.HTTP_403_FORBIDDEN,
        "CSRF validation failed",
    )


async def _user_not_found_handler(
    request: Request,
    error: Exception,
) -> JSONResponse:
    return _error_response(status.HTTP_404_NOT_FOUND, "User not found")


async def _role_not_found_handler(
    request: Request,
    error: Exception,
) -> JSONResponse:
    return _error_response(status.HTTP_404_NOT_FOUND, "Role not found")


async def _resource_not_found_handler(
    request: Request,
    error: Exception,
) -> JSONResponse:
    return _error_response(
        status.HTTP_404_NOT_FOUND,
        str(error) or "Resource not found",
    )


async def _resource_conflict_handler(
    request: Request,
    error: Exception,
) -> JSONResponse:
    return _error_response(
        status.HTTP_409_CONFLICT,
        str(error) or "Resource conflict",
    )


async def _permission_denied_handler(
    request: Request,
    error: Exception,
) -> JSONResponse:
    return _error_response(status.HTTP_403_FORBIDDEN, "Permission denied")


async def _invalid_analytics_request_handler(
    request: Request,
    error: Exception,
) -> JSONResponse:
    return _error_response(
        status.HTTP_422_UNPROCESSABLE_CONTENT,
        str(error) or "Invalid analytics request",
    )


async def _routing_error_handler(
    request: Request,
    error: Exception,
) -> JSONResponse:
    """Return a safe provider-routing failure instead of an uncorsed 500."""

    return _error_response(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "The orchestration provider is temporarily unavailable",
    )


async def _validation_error_handler(
    request: Request,
    error: Exception,
) -> JSONResponse:
    if not isinstance(error, RequestValidationError):
        raise error
    details = [
        {
            "type": item["type"],
            "loc": item["loc"],
            "msg": item["msg"],
        }
        for item in error.errors()
    ]
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"detail": details},
    )


def _error_response(
    status_code: int,
    detail: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"detail": detail},
        headers=headers,
    )
