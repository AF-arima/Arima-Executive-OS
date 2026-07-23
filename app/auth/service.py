from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.auth.exceptions import (
    DuplicateEmailError,
    InactiveUserError,
    InvalidCredentialsError,
    InvalidTokenError,
    RoleNotFoundError,
    TokenReuseError,
    UserNotFoundError,
)
from app.auth.passwords import hash_password, verify_password
from app.auth.roles import DEFAULT_USER_ROLE, seed_default_roles
from app.auth.tokens import JWTService
from app.core.config import Settings, get_settings
from app.database.models import RefreshTokenSession, User, UserRole
from app.database.repositories import (
    RefreshTokenRepository,
    RoleRepository,
    UserRepository,
)
from app.schemas.auth import UserLogin, UserRegistration

DUMMY_PASSWORD_HASH = hash_password("TimingOnly-Password-Not-A-Credential-1!")


@dataclass(frozen=True, slots=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int


class AuthenticationService:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.jwt = JWTService(self.settings)
        self.users = UserRepository(session)
        self.roles = RoleRepository(session)
        self.refresh_tokens = RefreshTokenRepository(session)

    async def register_user(self, data: UserRegistration) -> User:
        if await self.users.get_by_email(str(data.email)) is not None:
            raise DuplicateEmailError
        await self.session.rollback()

        password_hash = await run_in_threadpool(
            hash_password,
            data.password.get_secret_value(),
        )
        roles = await seed_default_roles(self.session)
        user = User(
            email=str(data.email),
            hashed_password=password_hash,
            first_name=data.first_name,
            last_name=data.last_name,
        )
        user.roles.append(roles[DEFAULT_USER_ROLE])
        self.session.add(user)

        try:
            await self.session.commit()
        except IntegrityError as error:
            await self.session.rollback()
            raise DuplicateEmailError from error
        return user

    async def authenticate_user(self, data: UserLogin) -> User:
        user = await self.users.get_by_email(str(data.email))
        password = data.password.get_secret_value()
        password_hash = user.hashed_password if user else DUMMY_PASSWORD_HASH
        user_id = user.id if user else None
        await self.session.rollback()

        password_valid = await run_in_threadpool(
            verify_password,
            password,
            password_hash,
        )
        if not password_valid or user_id is None:
            raise InvalidCredentialsError
        user = await self.users.get_with_roles(user_id)
        if user is None:
            raise InvalidCredentialsError
        if not user.is_active:
            raise InactiveUserError
        return user

    async def login(self, data: UserLogin) -> TokenPair:
        user = await self.authenticate_user(data)
        return await self.issue_token_pair(user)

    async def issue_token_pair(self, user: User) -> TokenPair:
        access = self.jwt.create_access_token(user.id)
        refresh = self.jwt.create_refresh_token(user.id)
        self.session.add(
            RefreshTokenSession(
                user_id=user.id,
                token_jti=str(refresh.claims.jti),
                expires_at=refresh.claims.expires_at,
            )
        )
        await self.session.commit()
        return TokenPair(
            access_token=access.value,
            refresh_token=refresh.value,
            expires_in=self.settings.access_token_expire_minutes * 60,
        )

    async def refresh_token_pair(self, refresh_token: str) -> TokenPair:
        claims = self.jwt.decode_token(
            refresh_token,
            expected_type="refresh",
        )
        now = datetime.now(timezone.utc)
        user_id = await self.refresh_tokens.revoke_active(
            claims.jti,
            revoked_at=now,
        )
        if user_id is None:
            raise TokenReuseError
        if user_id != claims.subject:
            raise InvalidTokenError

        user = await self.users.get_with_roles(user_id)
        if user is None:
            raise InvalidTokenError
        if not user.is_active:
            raise InactiveUserError

        return await self.issue_token_pair(user)

    async def logout(self, refresh_token: str) -> None:
        claims = self.jwt.decode_token(
            refresh_token,
            expected_type="refresh",
        )
        user_id = await self.refresh_tokens.revoke_active(
            claims.jti,
            revoked_at=datetime.now(timezone.utc),
        )
        if user_id is not None and user_id != claims.subject:
            raise InvalidTokenError
        await self.session.commit()

    async def get_current_user(self, user_id: UUID) -> User:
        user = await self.users.get_with_roles(user_id)
        if user is None:
            raise InvalidTokenError
        return user

    async def assign_role(self, user_id: UUID, role_name: str) -> User:
        user = await self.users.get_with_roles(user_id)
        if user is None:
            raise UserNotFoundError
        role = await self.roles.get_by_name(role_name)
        if role is None:
            raise RoleNotFoundError
        if all(existing.name != role.name for existing in user.roles):
            try:
                async with self.session.begin_nested():
                    self.session.add(
                        UserRole(user_id=user.id, role_id=role.id)
                    )
                    await self.session.flush()
            except IntegrityError as error:
                existing_link = await self.session.get(
                    UserRole,
                    (user.id, role.id),
                )
                if existing_link is None:
                    raise error
            await self.session.commit()
        updated_user = await self.users.get_with_roles(user_id)
        if updated_user is None:
            raise UserNotFoundError
        return updated_user

    async def remove_role(self, user_id: UUID, role_name: str) -> None:
        user = await self.users.get_with_roles(user_id)
        if user is None:
            raise UserNotFoundError
        role = next(
            (item for item in user.roles if item.name == role_name),
            None,
        )
        if role is not None:
            await self.session.execute(
                delete(UserRole).where(
                    UserRole.user_id == user.id,
                    UserRole.role_id == role.id,
                )
            )
            await self.session.commit()
