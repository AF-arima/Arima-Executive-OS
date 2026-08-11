from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.exceptions import (
    EmailNotVerifiedError,
    InactiveUserError,
    InvalidTokenError,
)
from app.auth.service import AuthenticationService
from app.auth.tokens import JWTService
from app.database.models import User
from app.database.session import get_session
from app.services.permissions import (
    has_founder_control_access,
    has_platform_administration,
)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")

SessionDependency = Annotated[AsyncSession, Depends(get_session)]
BearerToken = Annotated[str, Depends(oauth2_scheme)]


async def get_current_user(
    session: SessionDependency,
    token: BearerToken,
) -> User:
    claims = JWTService().decode_token(token, expected_type="access")
    if claims.session_id is None:
        raise InvalidTokenError
    return await AuthenticationService(session).get_current_user(
        claims.subject,
        session_id=claims.session_id,
    )


async def get_current_active_user(
    current_user: Annotated[User, Depends(get_current_user)],
) -> User:
    if not current_user.is_active:
        raise InactiveUserError
    if not current_user.is_verified:
        raise EmailNotVerifiedError
    return current_user


async def require_platform_operator(
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> User:
    if not has_platform_administration(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Platform operator access is required",
        )
    return current_user


async def require_founder_control(
    current_user: Annotated[User, Depends(get_current_active_user)],
) -> User:
    """Require a verified administrator on the server-side Founder allowlist."""

    if not has_founder_control_access(current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Founder access is required",
        )
    return current_user


def require_role(
    role_name: str,
) -> Callable[[User], Awaitable[User]]:
    async def dependency(
        current_user: Annotated[
            User,
            Depends(get_current_active_user),
        ],
    ) -> User:
        if all(role.name != role_name for role in current_user.roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return current_user

    return dependency


def require_any_role(
    *role_names: str,
) -> Callable[[User], Awaitable[User]]:
    required = frozenset(role_names)
    if not required:
        raise ValueError("At least one role is required")

    async def dependency(
        current_user: Annotated[
            User,
            Depends(get_current_active_user),
        ],
    ) -> User:
        if required.isdisjoint(role.name for role in current_user.roles):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions",
            )
        return current_user

    return dependency
