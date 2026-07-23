from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.auth.exceptions import (
    DuplicateEmailError,
    InactiveUserError,
    InvalidCredentialsError,
    InvalidTokenError,
    RoleNotFoundError,
    UserNotFoundError,
)

BEARER_HEADERS = {"WWW-Authenticate": "Bearer"}


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
    app.add_exception_handler(UserNotFoundError, _user_not_found_handler)
    app.add_exception_handler(RoleNotFoundError, _role_not_found_handler)
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
