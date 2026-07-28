"""Double-submit CSRF protection for cookie-authenticated session actions."""

import secrets

from fastapi import Request

from app.auth.exceptions import CsrfValidationError
from app.core.config import Settings, get_settings


def new_csrf_token() -> str:
    """Return a high-entropy value safe to expose to same-site JavaScript."""

    return secrets.token_urlsafe(32)


def require_valid_csrf(
    request: Request,
    settings: Settings | None = None,
) -> None:
    """Require matching cookie and request-header values for state changes."""

    active_settings = settings or get_settings()
    cookie_value = request.cookies.get(active_settings.auth_csrf_cookie_name)
    header_value = request.headers.get(active_settings.csrf_header_name)
    if (
        not cookie_value
        or not header_value
        or not secrets.compare_digest(cookie_value, header_value)
    ):
        raise CsrfValidationError
