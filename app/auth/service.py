from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from app.auth.context import RequestSecurityContext
from app.auth.exceptions import (
    AccountLockedError,
    DuplicateEmailError,
    EmailDeliveryError,
    EmailNotVerifiedError,
    InactiveUserError,
    InvalidCredentialsError,
    InvalidSecurityTokenError,
    InvalidTokenError,
    MFAAlreadyEnabledError,
    InvalidMFACodeError,
    MFALockedError,
    MFARequiredError,
    RoleNotFoundError,
    TokenReuseError,
    UserNotFoundError,
)
from app.auth.passwords import hash_password, verify_password
from app.auth.roles import DEFAULT_USER_ROLE, seed_default_roles
from app.auth.security import (
    SecurityRateLimiter,
    hash_security_token,
    new_security_token,
    record_security_event,
)
from app.auth.tokens import JWTService
from app.core.config import Settings, get_settings
from app.database.models import (
    AuditAction,
    AuditEntity,
    RefreshTokenSession,
    SecurityToken,
    SecurityTokenPurpose,
    User,
    UserRole,
    Tenant,
    Workspace,
    WorkspaceMembership,
)
from app.database.repositories import (
    RefreshTokenRepository,
    RoleRepository,
    SecurityTokenRepository,
    UserRepository,
)
from app.email.factory import get_transactional_email_service
from app.email.service import TransactionalEmailService
from app.services.audit import record_audit
from app.auth.totp import decrypt_secret, encrypt_secret, generate_secret, verify_code
from app.services.exceptions import PermissionDeniedError
from app.schemas.auth import (
    ChangeEmailRequest,
    ChangePasswordRequest,
    UserLogin,
    UserProfileUpdate,
    UserRegistration,
)

DUMMY_PASSWORD_HASH = hash_password("TimingOnly-Password-Not-A-Credential-1!")


@dataclass(frozen=True, slots=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int
    family_id: UUID
    refresh_expires_at: datetime
    is_persistent: bool


class AuthenticationService:
    """Transactional account, session, recovery, and profile operations."""

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings | None = None,
        email_service: TransactionalEmailService | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.email_service = email_service
        self.jwt = JWTService(self.settings)
        self.users = UserRepository(session)
        self.roles = RoleRepository(session)
        self.refresh_tokens = RefreshTokenRepository(session)
        self.security_tokens = SecurityTokenRepository(session)
        self.rate_limiter = SecurityRateLimiter(session, self.settings)

    async def register_user(
        self,
        data: UserRegistration,
        *,
        context: RequestSecurityContext | None = None,
    ) -> User:
        await self._limit(
            "registration",
            self._rate_key(context),
            self.settings.registration_rate_limit_per_hour,
            timedelta(hours=1),
        )
        email = str(data.email)
        if await self.users.get_by_email(email) is not None:
            raise DuplicateEmailError

        password_hash = await run_in_threadpool(
            hash_password, data.password.get_secret_value()
        )
        roles = await seed_default_roles(self.session)
        user = User(
            email=email,
            hashed_password=password_hash,
            first_name=data.first_name,
            last_name=data.last_name,
        )
        user.roles.append(roles[DEFAULT_USER_ROLE])
        tenant = Tenant(name=self._workspace_name(user))
        workspace = Workspace(name=self._workspace_name(user), tenant=tenant, owner=user)
        self.session.add_all(
            [
                user,
                WorkspaceMembership(workspace=workspace, user=user, role="owner"),
            ]
        )
        try:
            await self.session.flush()
            token = await self._issue_security_token(
                user,
                SecurityTokenPurpose.EMAIL_VERIFICATION,
                timedelta(hours=self.settings.verification_token_expire_hours),
            )
            self._event("account_registered", user, context)
            await self._email().send_verification(
                email=user.email,
                recipient_name=self._recipient_name(user),
                token=token,
            )
            await self.session.commit()
        except IntegrityError as error:
            await self.session.rollback()
            # A concurrent registration can still violate the unique email
            # constraint after the pre-flight lookup.  Do not, however,
            # misclassify an unrelated persistence failure as a duplicate
            # email: doing so masks schema and operational errors.
            if await self.users.get_by_email(email) is not None:
                raise DuplicateEmailError from error
            raise
        except EmailDeliveryError:
            await self.session.rollback()
            raise
        return user

    async def authenticate_user(
        self,
        data: UserLogin,
        *,
        context: RequestSecurityContext | None = None,
    ) -> User:
        user = await self.users.get_by_email(str(data.email))
        now = self._now()
        if user is not None and self._locked(user, now):
            raise AccountLockedError(self._lockout_seconds(user, now))
        if user is not None and user.locked_until is not None:
            user.failed_login_attempts = 0
            user.locked_until = None

        password_hash = user.hashed_password if user else DUMMY_PASSWORD_HASH
        valid = await run_in_threadpool(
            verify_password,
            data.password.get_secret_value(),
            password_hash,
        )
        if not valid or user is None:
            await self._failed_login(user, context)
            raise InvalidCredentialsError

        user = await self.users.get_with_roles(user.id)
        if user is None:
            raise InvalidCredentialsError
        if not user.is_active:
            self._event("login_rejected_inactive", user, context)
            await self.session.commit()
            raise InactiveUserError
        if not user.is_verified:
            self._event("login_rejected_unverified", user, context)
            await self.session.commit()
            raise EmailNotVerifiedError
        return user

    async def login(
        self,
        data: UserLogin,
        *,
        context: RequestSecurityContext | None = None,
    ) -> tuple[User, TokenPair]:
        await self._limit(
            "login",
            self._rate_key(context),
            self.settings.login_rate_limit_per_minute,
            timedelta(minutes=1),
        )
        user = await self.authenticate_user(data, context=context)
        await self._verify_login_mfa(user, data.otp)
        user.failed_login_attempts = 0
        user.locked_until = None
        user.last_login_at = self._now()
        user.last_login_ip = context.ip_address if context else None
        self._event("login_succeeded", user, context)
        pair = await self.issue_token_pair(
            user,
            remember_me=data.remember_me,
            context=context,
        )
        await self._login_notification(user, context)
        return user, pair

    async def begin_mfa_enrollment(self, user: User) -> tuple[str, str]:
        await self._limit(
            "mfa_enrollment_begin",
            str(user.id),
            self.settings.login_rate_limit_per_minute,
            timedelta(minutes=1),
        )
        if user.mfa_enabled:
            # Re-enrollment would let a stolen privileged session replace the
            # existing factor without proving the current factor. Recovery is
            # intentionally an audited operational process, not a bypass here.
            raise MFAAlreadyEnabledError
        secret = generate_secret()
        user.mfa_secret_encrypted = encrypt_secret(secret)
        user.mfa_enabled = False
        user.mfa_last_accepted_step = None
        user.mfa_failed_attempts = 0
        await self.session.commit()
        label = f"ARIMA:{user.email}"
        uri = "otpauth://totp/" + label.replace(" ", "%20") + f"?secret={secret}&issuer=ARIMA"
        return secret, uri

    async def confirm_mfa_enrollment(self, user: User, code: str) -> None:
        await self._limit(
            "mfa_enrollment",
            str(user.id),
            self.settings.login_rate_limit_per_minute,
            timedelta(minutes=1),
        )
        if not user.mfa_secret_encrypted:
            raise InvalidMFACodeError
        secret = decrypt_secret(user.mfa_secret_encrypted)
        step = verify_code(secret, code, last_step=user.mfa_last_accepted_step)
        if step is None:
            user.mfa_failed_attempts += 1
            if user.mfa_failed_attempts >= self.settings.privileged_mfa_max_attempts:
                user.mfa_locked_until = self._now() + timedelta(minutes=self.settings.privileged_mfa_lockout_minutes)
                user.mfa_failed_attempts = 0
                await self.session.commit()
                raise MFALockedError(self.settings.privileged_mfa_lockout_minutes * 60)
            await self.session.commit()
            raise InvalidMFACodeError
        user.mfa_enabled = True
        user.mfa_last_accepted_step = step
        user.mfa_failed_attempts = 0
        await self.refresh_tokens.revoke_all_for_user(
            user.id, revoked_at=self._now(), reason="mfa_enabled"
        )
        record_audit(self.session, actor_id=user.id, action=AuditAction.STATUS_CHANGE, entity=AuditEntity.ACCOUNT, entity_id=user.id, event_type="MFA_ENABLED")
        await self.session.commit()

    async def recover_mfa(
        self,
        actor: User,
        target_user_id: UUID,
        *,
        reason: str,
    ) -> None:
        """Clear a target's factor only through an already-authorized operator.

        This is deliberately not self-service. The route requires Founder
        Control, which in production requires the operator's existing MFA.
        The supplied reason is validated for operator accountability but is
        not persisted because free text must never become a secret sink.
        """
        if actor.id == target_user_id:
            raise PermissionDeniedError("MFA recovery cannot be self-service")
        target = await self.session.get(User, target_user_id, with_for_update=True)
        if target is None:
            raise UserNotFoundError
        if not target.mfa_enabled and not target.mfa_secret_encrypted:
            raise PermissionDeniedError("Target has no enrolled MFA factor")
        if len(reason.strip()) < 8:
            raise PermissionDeniedError("A recovery reason is required")
        target.mfa_secret_encrypted = None
        target.mfa_enabled = False
        target.mfa_last_accepted_step = None
        target.mfa_failed_attempts = 0
        target.mfa_locked_until = None
        now = self._now()
        await self.refresh_tokens.revoke_all_for_user(
            target.id,
            revoked_at=now,
            reason="mfa_recovery",
        )
        record_audit(
            self.session,
            actor_id=actor.id,
            action=AuditAction.STATUS_CHANGE,
            entity=AuditEntity.ACCOUNT,
            entity_id=target.id,
            event_type="MFA_RECOVERY",
            event_metadata={
                "target_user_id": str(target.id),
                "sessions_revoked": True,
                "reason_recorded": True,
            },
        )
        await self.session.commit()

    async def _verify_login_mfa(self, user: User, otp: object | None) -> None:
        await self._limit(
            "mfa_login",
            str(user.id),
            self.settings.login_rate_limit_per_minute,
            timedelta(minutes=1),
        )
        if not user.mfa_enabled:
            return
        now = self._now()
        if user.mfa_locked_until is not None and user.mfa_locked_until > now:
            raise MFALockedError(max(1, int((user.mfa_locked_until - now).total_seconds())))
        if otp is None:
            raise MFARequiredError
        try:
            secret = decrypt_secret(user.mfa_secret_encrypted or "")
        except Exception as error:
            raise MFARequiredError from error
        step = verify_code(secret, str(getattr(otp, "get_secret_value", lambda: otp)()), last_step=user.mfa_last_accepted_step)
        if step is None:
            user.mfa_failed_attempts += 1
            if user.mfa_failed_attempts >= self.settings.privileged_mfa_max_attempts:
                user.mfa_locked_until = now + timedelta(minutes=self.settings.privileged_mfa_lockout_minutes)
                user.mfa_failed_attempts = 0
                await self.session.commit()
                raise MFALockedError(self.settings.privileged_mfa_lockout_minutes * 60)
            await self.session.commit()
            raise InvalidMFACodeError
        user.mfa_failed_attempts = 0
        user.mfa_locked_until = None
        user.mfa_last_accepted_step = step

    async def issue_token_pair(
        self,
        user: User,
        *,
        remember_me: bool = False,
        context: RequestSecurityContext | None = None,
        family_id: UUID | None = None,
        parent_jti: str | None = None,
    ) -> TokenPair:
        active_family_id = family_id or uuid4()
        refresh_lifetime = self._refresh_lifetime(remember_me)
        access = self.jwt.create_access_token(
            user.id, session_id=active_family_id
        )
        refresh = self.jwt.create_refresh_token(
            user.id,
            expires_delta=refresh_lifetime,
            session_id=active_family_id,
        )
        self.session.add(
            RefreshTokenSession(
                user_id=user.id,
                token_jti=str(refresh.claims.jti),
                family_id=active_family_id,
                parent_jti=parent_jti,
                is_persistent=remember_me,
                user_agent=context.user_agent if context else None,
                ip_address=context.ip_address if context else None,
                expires_at=refresh.claims.expires_at,
            )
        )
        await self.session.commit()
        return TokenPair(
            access_token=access.value,
            refresh_token=refresh.value,
            expires_in=self.settings.access_token_expire_minutes * 60,
            family_id=active_family_id,
            refresh_expires_at=refresh.claims.expires_at,
            is_persistent=remember_me,
        )

    async def refresh_token_pair(
        self,
        refresh_token: str,
        *,
        context: RequestSecurityContext | None = None,
    ) -> TokenPair:
        claims = self.jwt.decode_token(refresh_token, expected_type="refresh")
        if claims.session_id is None:
            raise InvalidTokenError
        now = self._now()
        active = await self.refresh_tokens.consume_active(
            claims.jti, revoked_at=now
        )
        if active is None:
            await self.refresh_tokens.revoke_family(
                claims.session_id,
                revoked_at=now,
                reason="token_reuse_detected",
            )
            record_security_event(
                self.session,
                event_type="refresh_token_reuse_detected",
                user_id=claims.subject,
                ip_address=context.ip_address if context else None,
                user_agent=context.user_agent if context else None,
                metadata={"family_id": str(claims.session_id)},
            )
            await self.session.commit()
            raise TokenReuseError
        if active.user_id != claims.subject or active.family_id != claims.session_id:
            await self.session.rollback()
            raise InvalidTokenError

        user = await self.users.get_with_roles(claims.subject)
        if user is None:
            await self.session.rollback()
            raise InvalidTokenError
        if not user.is_active:
            await self.session.rollback()
            raise InactiveUserError
        if not user.is_verified:
            await self.session.rollback()
            raise EmailNotVerifiedError
        self._event("session_refreshed", user, context)
        return await self.issue_token_pair(
            user,
            remember_me=active.is_persistent,
            context=context,
            family_id=active.family_id,
            parent_jti=active.token_jti,
        )

    async def logout(
        self,
        refresh_token: str,
        *,
        context: RequestSecurityContext | None = None,
    ) -> None:
        claims = self.jwt.decode_token(refresh_token, expected_type="refresh")
        if claims.session_id is None:
            raise InvalidTokenError
        now = self._now()
        active = await self.refresh_tokens.consume_active(
            claims.jti, revoked_at=now
        )
        if active is None:
            await self.refresh_tokens.revoke_family(
                claims.session_id,
                revoked_at=now,
                reason="logout_reused_token",
            )
            await self.session.commit()
            return
        if active.user_id != claims.subject or active.family_id != claims.session_id:
            await self.session.rollback()
            raise InvalidTokenError
        await self.refresh_tokens.revoke_family(
            active.family_id, revoked_at=now, reason="user_logout"
        )
        record_security_event(
            self.session,
            event_type="logout_succeeded",
            user_id=active.user_id,
            ip_address=context.ip_address if context else None,
            user_agent=context.user_agent if context else None,
        )
        record_audit(
            self.session,
            actor_id=active.user_id,
            action=AuditAction.STATUS_CHANGE,
            entity=AuditEntity.ACCOUNT,
            entity_id=active.user_id,
            event_type="SESSION_REVOKED",
            event_metadata={"reason": "user_logout"},
        )
        await self.session.commit()

    async def logout_all(
        self,
        user: User,
        *,
        context: RequestSecurityContext | None = None,
        reason: str = "user_logout_all",
    ) -> None:
        await self.refresh_tokens.revoke_all_for_user(
            user.id, revoked_at=self._now(), reason=reason
        )
        self._event("logout_all_succeeded", user, context)
        record_audit(
            self.session,
            actor_id=user.id,
            action=AuditAction.STATUS_CHANGE,
            entity=AuditEntity.ACCOUNT,
            entity_id=user.id,
            event_type="SESSION_REVOKED",
            event_metadata={"reason": reason},
        )
        await self.session.commit()

    async def list_sessions(self, user: User) -> list[RefreshTokenSession]:
        return await self.refresh_tokens.list_active_for_user(
            user.id, now=self._now()
        )

    async def revoke_session(
        self,
        user: User,
        family_id: UUID,
        *,
        context: RequestSecurityContext | None = None,
    ) -> None:
        sessions = await self.list_sessions(user)
        if not any(session.family_id == family_id for session in sessions):
            raise InvalidTokenError
        await self.refresh_tokens.revoke_family(
            family_id, revoked_at=self._now(), reason="user_revoked_session"
        )
        self._event(
            "session_revoked",
            user,
            context,
            metadata={"family_id": str(family_id)},
        )
        await self.session.commit()

    async def get_current_user(
        self,
        user_id: UUID,
        *,
        session_id: UUID | None = None,
    ) -> User:
        user = await self.users.get_with_roles(user_id)
        if user is None:
            raise InvalidTokenError
        if session_id is not None and not await self.refresh_tokens.has_active_family(
            session_id, now=self._now()
        ):
            raise InvalidTokenError
        return user

    async def resend_verification(
        self,
        email: str,
        *,
        context: RequestSecurityContext | None = None,
    ) -> None:
        await self._limit(
            "verification_resend",
            f"{self._rate_key(context)}:{email}",
            self.settings.password_reset_rate_limit_per_hour,
            timedelta(hours=1),
        )
        user = await self.users.get_by_email(email)
        if user is None or user.is_verified or not user.is_active:
            return
        token = await self._issue_security_token(
            user,
            SecurityTokenPurpose.EMAIL_VERIFICATION,
            timedelta(hours=self.settings.verification_token_expire_hours),
        )
        self._event("email_verification_resent", user, context)
        try:
            await self._email().send_verification(
                email=user.email,
                recipient_name=self._recipient_name(user),
                token=token,
            )
            await self.session.commit()
        except EmailDeliveryError:
            await self.session.rollback()
            raise

    async def verify_email(
        self,
        token: str,
        *,
        context: RequestSecurityContext | None = None,
    ) -> User:
        user = await self._consume_token(
            token, SecurityTokenPurpose.EMAIL_VERIFICATION
        )
        user.is_verified = True
        self._event("email_verified", user, context)
        await self.session.commit()
        await self._best_effort_email(user, context, "welcome")
        return user

    async def request_password_reset(
        self,
        email: str,
        *,
        context: RequestSecurityContext | None = None,
    ) -> None:
        await self._limit(
            "password_reset",
            f"{self._rate_key(context)}:{email}",
            self.settings.password_reset_rate_limit_per_hour,
            timedelta(hours=1),
        )
        user = await self.users.get_by_email(email)
        if user is None or not user.is_active:
            return
        token = await self._issue_security_token(
            user,
            SecurityTokenPurpose.PASSWORD_RESET,
            timedelta(minutes=self.settings.password_reset_token_expire_minutes),
        )
        self._event("password_reset_requested", user, context)
        try:
            await self._email().send_password_reset(
                email=user.email,
                recipient_name=self._recipient_name(user),
                token=token,
            )
            await self.session.commit()
        except EmailDeliveryError:
            await self.session.rollback()
            raise

    async def reset_password(
        self,
        token: str,
        password: str,
        *,
        context: RequestSecurityContext | None = None,
    ) -> None:
        user = await self._consume_token(token, SecurityTokenPurpose.PASSWORD_RESET)
        user.hashed_password = await run_in_threadpool(hash_password, password)
        user.password_changed_at = self._now()
        user.failed_login_attempts = 0
        user.locked_until = None
        await self.refresh_tokens.revoke_all_for_user(
            user.id, revoked_at=self._now(), reason="password_reset"
        )
        self._event("password_reset_completed", user, context)
        record_audit(
            self.session,
            actor_id=user.id,
            action=AuditAction.STATUS_CHANGE,
            entity=AuditEntity.ACCOUNT,
            entity_id=user.id,
            event_type="PASSWORD_CHANGED",
            event_metadata={"reason": "password_reset"},
        )
        await self.session.commit()
        await self._best_effort_email(user, context, "password reset")

    async def change_password(
        self,
        user: User,
        data: ChangePasswordRequest,
        *,
        context: RequestSecurityContext | None = None,
    ) -> None:
        await self._limit(
            "password_change",
            self._rate_key(context) + ":" + str(user.id),
            self.settings.password_reset_rate_limit_per_hour,
            timedelta(hours=1),
        )
        valid = await run_in_threadpool(
            verify_password,
            data.current_password.get_secret_value(),
            user.hashed_password,
        )
        if not valid:
            self._event("password_change_rejected", user, context)
            await self.session.commit()
            raise InvalidCredentialsError
        if await run_in_threadpool(
            verify_password,
            data.password.get_secret_value(),
            user.hashed_password,
        ):
            raise InvalidCredentialsError
        user.hashed_password = await run_in_threadpool(
            hash_password, data.password.get_secret_value()
        )
        user.password_changed_at = self._now()
        record_audit(
            self.session,
            actor_id=user.id,
            action=AuditAction.STATUS_CHANGE,
            entity=AuditEntity.ACCOUNT,
            entity_id=user.id,
            event_type="PASSWORD_CHANGED",
        )
        await self.refresh_tokens.revoke_all_for_user(
            user.id, revoked_at=self._now(), reason="password_changed"
        )
        self._event("password_changed", user, context)
        await self.session.commit()
        await self._best_effort_email(user, context, "password changed")

    async def request_email_change(
        self,
        user: User,
        data: ChangeEmailRequest,
        *,
        context: RequestSecurityContext | None = None,
    ) -> None:
        target_email = str(data.new_email)
        if target_email == user.email or await self.users.get_by_email(target_email):
            raise DuplicateEmailError
        valid = await run_in_threadpool(
            verify_password,
            data.current_password.get_secret_value(),
            user.hashed_password,
        )
        if not valid:
            self._event("email_change_rejected", user, context)
            await self.session.commit()
            raise InvalidCredentialsError
        previous_email = user.email
        token = await self._issue_security_token(
            user,
            SecurityTokenPurpose.EMAIL_CHANGE,
            timedelta(hours=self.settings.email_change_token_expire_hours),
            target_email=target_email,
        )
        self._event(
            "email_change_requested",
            user,
            context,
            metadata={"target_email": target_email},
        )
        try:
            await self._email().send_email_change(
                email=target_email,
                recipient_name=self._recipient_name(user),
                token=token,
            )
            await self.session.commit()
        except EmailDeliveryError:
            await self.session.rollback()
            raise
        await self._best_effort_email(
            user,
            context,
            "email change requested",
            recipient_email=previous_email,
        )

    async def confirm_email_change(
        self,
        token: str,
        *,
        context: RequestSecurityContext | None = None,
    ) -> User:
        security_token = await self._token_record(
            token, SecurityTokenPurpose.EMAIL_CHANGE
        )
        target_email = security_token.target_email
        if target_email is None:
            await self.session.rollback()
            raise InvalidSecurityTokenError
        user = await self.users.get_with_roles(security_token.user_id)
        if user is None:
            await self.session.rollback()
            raise InvalidSecurityTokenError
        existing = await self.users.get_by_email(target_email)
        if existing is not None and existing.id != user.id:
            await self.session.rollback()
            raise DuplicateEmailError
        previous_email = user.email
        security_token.consumed_at = self._now()
        user.email = target_email
        user.is_verified = True
        await self.refresh_tokens.revoke_all_for_user(
            user.id, revoked_at=self._now(), reason="email_changed"
        )
        self._event("email_changed", user, context)
        try:
            await self.session.commit()
        except IntegrityError as error:
            await self.session.rollback()
            raise DuplicateEmailError from error
        await self._best_effort_email(user, context, "email changed")
        await self._best_effort_email(
            user,
            context,
            "email changed",
            recipient_email=previous_email,
        )
        return user

    async def update_profile(
        self, user: User, data: UserProfileUpdate
    ) -> User:
        if data.first_name is not None:
            user.first_name = data.first_name
        if data.last_name is not None:
            user.last_name = data.last_name
        await self.session.commit()
        updated = await self.users.get_with_roles(user.id)
        if updated is None:
            raise UserNotFoundError
        return updated

    async def assign_role(
        self,
        user_id: UUID,
        role_name: str,
        *,
        actor: User | None = None,
    ) -> User:
        user = await self.users.get_with_roles(user_id)
        if user is None:
            raise UserNotFoundError
        role = await self.roles.get_by_name(role_name)
        if role is None:
            raise RoleNotFoundError
        changed = False
        if all(existing.name != role.name for existing in user.roles):
            try:
                async with self.session.begin_nested():
                    self.session.add(UserRole(user_id=user.id, role_id=role.id))
                    await self.session.flush()
            except IntegrityError as error:
                existing_link = await self.session.get(UserRole, (user.id, role.id))
                if existing_link is None:
                    raise error
            changed = True
        if actor is not None:
            self._event(
                "platform_role_assigned",
                actor,
                None,
                metadata={"user_id": str(user.id), "role": role.name},
            )
        if changed or actor is not None:
            await self.session.commit()
        updated = await self.users.get_with_roles(user_id)
        if updated is None:
            raise UserNotFoundError
        return updated

    async def remove_role(
        self,
        user_id: UUID,
        role_name: str,
        *,
        actor: User | None = None,
    ) -> None:
        user = await self.users.get_with_roles(user_id)
        if user is None:
            raise UserNotFoundError
        role = next((item for item in user.roles if item.name == role_name), None)
        if role is not None:
            await self.session.execute(
                delete(UserRole).where(
                    UserRole.user_id == user.id,
                    UserRole.role_id == role.id,
                )
            )
        if actor is not None:
            self._event(
                "platform_role_removed",
                actor,
                None,
                metadata={"user_id": str(user.id), "role": role_name},
            )
        if role is not None or actor is not None:
            await self.session.commit()

    async def _issue_security_token(
        self,
        user: User,
        purpose: SecurityTokenPurpose,
        lifetime: timedelta,
        *,
        target_email: str | None = None,
    ) -> str:
        now = self._now()
        raw_token = new_security_token()
        await self.security_tokens.invalidate_active(user.id, purpose, now=now)
        self.session.add(
            SecurityToken(
                user_id=user.id,
                purpose=purpose,
                token_hash=hash_security_token(raw_token, self.settings),
                target_email=target_email,
                expires_at=now + lifetime,
            )
        )
        await self.session.flush()
        return raw_token

    async def _consume_token(
        self, token: str, purpose: SecurityTokenPurpose
    ) -> User:
        security_token = await self._token_record(token, purpose)
        user = await self.users.get_with_roles(security_token.user_id)
        if user is None:
            await self.session.rollback()
            raise InvalidSecurityTokenError
        security_token.consumed_at = self._now()
        return user

    async def _token_record(
        self, token: str, purpose: SecurityTokenPurpose
    ) -> SecurityToken:
        security_token = await self.security_tokens.get_active_for_consumption(
            hash_security_token(token, self.settings), purpose, now=self._now()
        )
        if security_token is None:
            raise InvalidSecurityTokenError
        return security_token

    async def _failed_login(
        self, user: User | None, context: RequestSecurityContext | None
    ) -> None:
        if user is None:
            record_security_event(
                self.session,
                event_type="login_failed_unknown_account",
                ip_address=context.ip_address if context else None,
                user_agent=context.user_agent if context else None,
            )
            await self.session.commit()
            return
        user.failed_login_attempts += 1
        metadata: dict[str, object] = {"failed_attempts": user.failed_login_attempts}
        if user.failed_login_attempts >= self.settings.max_failed_login_attempts:
            user.locked_until = self._now() + timedelta(
                minutes=self.settings.account_lockout_minutes
            )
            metadata["locked"] = True
        self._event("login_failed", user, context, metadata=metadata)
        await self.session.commit()
        if user.locked_until is not None:
            raise AccountLockedError(self._lockout_seconds(user, self._now()))

    async def _limit(
        self, scope: str, key: str, limit: int, window: timedelta
    ) -> None:
        await self.rate_limiter.enforce(
            scope=scope, key=key, limit=limit, window=window
        )

    async def _login_notification(
        self, user: User, context: RequestSecurityContext | None
    ) -> None:
        try:
            await self._email().send_login_notification(
                email=user.email,
                recipient_name=self._recipient_name(user),
                ip_address=context.ip_address if context else None,
                user_agent=context.user_agent if context else None,
            )
        except EmailDeliveryError:
            self._event("login_notification_delivery_failed", user, context)
            await self.session.commit()

    async def _best_effort_email(
        self,
        user: User,
        context: RequestSecurityContext | None,
        event: str,
        *,
        recipient_email: str | None = None,
    ) -> None:
        try:
            email = recipient_email or user.email
            if event == "welcome":
                await self._email().send_welcome(
                    email=email, recipient_name=self._recipient_name(user)
                )
            else:
                await self._email().send_security_alert(
                    email=email,
                    recipient_name=self._recipient_name(user),
                    event=event,
                )
        except EmailDeliveryError:
            self._event(
                "security_email_delivery_failed",
                user,
                context,
                metadata={"event": event},
            )
            await self.session.commit()

    def _email(self) -> TransactionalEmailService:
        return self.email_service or get_transactional_email_service()

    def _event(
        self,
        event_type: str,
        user: User,
        context: RequestSecurityContext | None,
        *,
        metadata: dict[str, object] | None = None,
    ) -> None:
        record_security_event(
            self.session,
            event_type=event_type,
            user_id=user.id,
            ip_address=context.ip_address if context else None,
            user_agent=context.user_agent if context else None,
            metadata=metadata,
        )

    def _refresh_lifetime(self, remember_me: bool) -> timedelta:
        if remember_me:
            return timedelta(days=self.settings.refresh_token_expire_days)
        return timedelta(hours=self.settings.session_refresh_token_expire_hours)

    @staticmethod
    def _recipient_name(user: User) -> str:
        return f"{user.first_name} {user.last_name}".strip()

    @classmethod
    def _workspace_name(cls, user: User) -> str:
        suffix = " Workspace"
        name = cls._recipient_name(user) or "Arima"
        return f"{name[: 160 - len(suffix)]}{suffix}"

    @staticmethod
    def _rate_key(context: RequestSecurityContext | None) -> str:
        return context.ip_address if context and context.ip_address else "unknown"

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    @staticmethod
    def _locked(user: User, now: datetime) -> bool:
        return (
            user.locked_until is not None
            and AuthenticationService._as_utc(user.locked_until) > now
        )

    @staticmethod
    def _lockout_seconds(user: User, now: datetime) -> int:
        if user.locked_until is None:
            return 1
        return max(
            1,
            int((AuthenticationService._as_utc(user.locked_until) - now).total_seconds()),
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
