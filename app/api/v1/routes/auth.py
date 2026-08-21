from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.context import request_security_context
from app.auth.csrf import new_csrf_token, require_valid_csrf
from app.auth.dependencies import (
    get_current_active_user,
    require_founder_control,
    require_founder_enrollment_access,
)
from app.auth.exceptions import InvalidTokenError
from app.auth.login import login_credentials
from app.auth.security import SecurityRateLimiter
from app.auth.service import AuthenticationService, TokenPair
from app.core.config import get_settings
from app.database.models import RefreshTokenSession, User
from app.database.session import get_session
from app.email.factory import get_transactional_email_service
from app.email.service import TransactionalEmailService
from app.schemas.auth import (
    AuthSessionResponse,
    ChangeEmailRequest,
    ChangePasswordRequest,
    CsrfTokenResponse,
    CurrentUserResponse,
    EmailAddressRequest,
    PasswordResetRequest,
    RegistrationResponse,
    SecurityTokenRequest,
    SessionListResponse,
    SessionResponse,
    UserLogin,
    UserPublicResponse,
    UserProfileUpdate,
    UserRegistration,
    MFAEnrollmentResponse,
    MFACodeRequest,
    MFARecoveryRequest,
)

router = APIRouter(prefix="/auth", tags=["authentication"])
SessionDependency = Annotated[AsyncSession, Depends(get_session)]
EmailServiceDependency = Annotated[
    TransactionalEmailService,
    Depends(get_transactional_email_service),
]
CurrentUserDependency = Annotated[User, Depends(get_current_active_user)]
FounderEnrollmentDependency = Annotated[User, Depends(require_founder_enrollment_access)]
FounderControlDependency = Annotated[User, Depends(require_founder_control)]


@router.post("/csrf", response_model=CsrfTokenResponse)
async def csrf_token(response: Response) -> CsrfTokenResponse:
    token = new_csrf_token()
    _set_csrf_cookie(response, token)
    _set_no_store(response)
    return CsrfTokenResponse(csrf_token=token)


@router.post("/mfa/enroll", response_model=MFAEnrollmentResponse)
async def enroll_mfa(
    request: Request,
    current_user: FounderEnrollmentDependency,
    session: SessionDependency,
) -> MFAEnrollmentResponse:
    require_valid_csrf(request)
    _, uri = await AuthenticationService(session).begin_mfa_enrollment(current_user)
    return MFAEnrollmentResponse(enabled=False, otpauth_uri=uri)


@router.post("/mfa/verify", status_code=status.HTTP_204_NO_CONTENT)
async def verify_mfa(
    data: MFACodeRequest,
    request: Request,
    response: Response,
    current_user: FounderEnrollmentDependency,
    session: SessionDependency,
) -> Response:
    require_valid_csrf(request)
    await AuthenticationService(session).confirm_mfa_enrollment(current_user, data.code)
    response.status_code = status.HTTP_204_NO_CONTENT
    _set_no_store(response)
    return response


@router.post("/mfa/recover/{target_user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def recover_mfa(
    target_user_id: UUID,
    data: MFARecoveryRequest,
    request: Request,
    response: Response,
    actor: FounderControlDependency,
    session: SessionDependency,
) -> Response:
    require_valid_csrf(request)
    await AuthenticationService(session).recover_mfa(
        actor,
        target_user_id,
        reason=data.reason,
    )
    _set_no_store(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post(
    "/register",
    response_model=RegistrationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register(
    data: UserRegistration,
    request: Request,
    response: Response,
    session: SessionDependency,
    email_service: EmailServiceDependency,
) -> RegistrationResponse:
    require_valid_csrf(request)
    user = await AuthenticationService(session, email_service=email_service).register_user(
        data,
        context=request_security_context(request),
    )
    _set_no_store(response)
    return RegistrationResponse(user=UserPublicResponse.model_validate(user))


@router.post("/login", response_model=AuthSessionResponse)
async def login(
    data: Annotated[UserLogin, Depends(login_credentials)],
    request: Request,
    response: Response,
    session: SessionDependency,
    email_service: EmailServiceDependency,
) -> AuthSessionResponse:
    require_valid_csrf(request)
    user, pair = await AuthenticationService(
        session,
        email_service=email_service,
    ).login(data, context=request_security_context(request))
    csrf = new_csrf_token()
    _set_refresh_cookie(response, pair)
    _set_csrf_cookie(response, csrf)
    _set_no_store(response)
    return _session_response(user, pair, csrf)


@router.post("/refresh", response_model=AuthSessionResponse)
async def refresh(
    request: Request,
    response: Response,
    session: SessionDependency,
) -> AuthSessionResponse:
    require_valid_csrf(request)
    raw_refresh = request.cookies.get(get_settings().auth_refresh_cookie_name)
    if not raw_refresh:
        from app.auth.exceptions import InvalidTokenError

        raise InvalidTokenError
    context = request_security_context(request)
    settings = get_settings()
    await SecurityRateLimiter(session, settings).enforce(
        scope="auth_refresh",
        key=context.ip_address or "unknown-client",
        limit=settings.login_rate_limit_per_minute,
        window=timedelta(minutes=1),
    )
    service = AuthenticationService(session, settings=settings)
    pair = await service.refresh_token_pair(
        raw_refresh,
        context=context,
    )
    user = await service.get_current_user(
        service.jwt.decode_token(pair.access_token, expected_type="access").subject,
        session_id=pair.family_id,
    )
    csrf = new_csrf_token()
    _set_refresh_cookie(response, pair)
    _set_csrf_cookie(response, csrf)
    _set_no_store(response)
    return _session_response(user, pair, csrf)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    session: SessionDependency,
) -> Response:
    require_valid_csrf(request)
    raw_refresh = request.cookies.get(get_settings().auth_refresh_cookie_name)
    if raw_refresh:
        try:
            await AuthenticationService(session).logout(
                raw_refresh,
                context=request_security_context(request),
            )
        except InvalidTokenError:
            pass
    _clear_auth_cookies(response)
    _set_no_store(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/logout-all", status_code=status.HTTP_204_NO_CONTENT)
async def logout_all(
    request: Request,
    response: Response,
    current_user: CurrentUserDependency,
    session: SessionDependency,
) -> Response:
    require_valid_csrf(request)
    await AuthenticationService(session).logout_all(
        current_user,
        context=request_security_context(request),
    )
    _clear_auth_cookies(response)
    _set_no_store(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/me", response_model=CurrentUserResponse)
async def me(
    current_user: CurrentUserDependency,
    response: Response,
) -> User:
    _set_no_store(response)
    return current_user


@router.patch("/me", response_model=CurrentUserResponse)
async def update_me(
    data: UserProfileUpdate,
    request: Request,
    response: Response,
    current_user: CurrentUserDependency,
    session: SessionDependency,
) -> User:
    require_valid_csrf(request)
    user = await AuthenticationService(session).update_profile(current_user, data)
    _set_no_store(response)
    return user


@router.get("/sessions", response_model=SessionListResponse)
async def sessions(
    current_user: CurrentUserDependency,
    session: SessionDependency,
    request: Request,
    response: Response,
) -> SessionListResponse:
    active = await AuthenticationService(session).list_sessions(current_user)
    current_family = _current_family_id(request)
    _set_no_store(response)
    return SessionListResponse(
        items=[_session_response_item(item, current_family) for item in active]
    )


@router.delete("/sessions/{family_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_session(
    family_id: UUID,
    request: Request,
    response: Response,
    current_user: CurrentUserDependency,
    session: SessionDependency,
) -> Response:
    require_valid_csrf(request)
    await AuthenticationService(session).revoke_session(
        current_user,
        family_id,
        context=request_security_context(request),
    )
    if _current_family_id(request) == family_id:
        _clear_auth_cookies(response)
    _set_no_store(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/verify-email", response_model=CurrentUserResponse)
async def verify_email(
    data: SecurityTokenRequest,
    request: Request,
    response: Response,
    session: SessionDependency,
    email_service: EmailServiceDependency,
) -> User:
    require_valid_csrf(request)
    user = await AuthenticationService(session, email_service=email_service).verify_email(
        data.token.get_secret_value(),
        context=request_security_context(request),
    )
    _set_no_store(response)
    return user


@router.post("/verify-email/resend", status_code=status.HTTP_202_ACCEPTED)
async def resend_verification(
    data: EmailAddressRequest,
    request: Request,
    response: Response,
    session: SessionDependency,
    email_service: EmailServiceDependency,
) -> Response:
    require_valid_csrf(request)
    await AuthenticationService(
        session,
        email_service=email_service,
    ).resend_verification(
        str(data.email),
        context=request_security_context(request),
    )
    _set_no_store(response)
    response.status_code = status.HTTP_202_ACCEPTED
    return response


@router.post("/forgot-password", status_code=status.HTTP_202_ACCEPTED)
async def forgot_password(
    data: EmailAddressRequest,
    request: Request,
    response: Response,
    session: SessionDependency,
    email_service: EmailServiceDependency,
) -> Response:
    require_valid_csrf(request)
    await AuthenticationService(
        session,
        email_service=email_service,
    ).request_password_reset(
        str(data.email),
        context=request_security_context(request),
    )
    _set_no_store(response)
    response.status_code = status.HTTP_202_ACCEPTED
    return response


@router.post("/reset-password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_password(
    data: PasswordResetRequest,
    request: Request,
    response: Response,
    session: SessionDependency,
    email_service: EmailServiceDependency,
) -> Response:
    require_valid_csrf(request)
    await AuthenticationService(
        session,
        email_service=email_service,
    ).reset_password(
        data.token.get_secret_value(),
        data.password.get_secret_value(),
        context=request_security_context(request),
    )
    _clear_auth_cookies(response)
    _set_no_store(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    data: ChangePasswordRequest,
    request: Request,
    response: Response,
    current_user: CurrentUserDependency,
    session: SessionDependency,
    email_service: EmailServiceDependency,
) -> Response:
    require_valid_csrf(request)
    await AuthenticationService(
        session,
        email_service=email_service,
    ).change_password(
        current_user,
        data,
        context=request_security_context(request),
    )
    _clear_auth_cookies(response)
    _set_no_store(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/change-email", status_code=status.HTTP_202_ACCEPTED)
async def change_email(
    data: ChangeEmailRequest,
    request: Request,
    response: Response,
    current_user: CurrentUserDependency,
    session: SessionDependency,
    email_service: EmailServiceDependency,
) -> Response:
    require_valid_csrf(request)
    await AuthenticationService(
        session,
        email_service=email_service,
    ).request_email_change(
        current_user,
        data,
        context=request_security_context(request),
    )
    _set_no_store(response)
    response.status_code = status.HTTP_202_ACCEPTED
    return response


@router.post("/change-email/confirm", response_model=CurrentUserResponse)
async def confirm_email_change(
    data: SecurityTokenRequest,
    request: Request,
    response: Response,
    session: SessionDependency,
    email_service: EmailServiceDependency,
) -> User:
    require_valid_csrf(request)
    user = await AuthenticationService(
        session,
        email_service=email_service,
    ).confirm_email_change(
        data.token.get_secret_value(),
        context=request_security_context(request),
    )
    _clear_auth_cookies(response)
    _set_no_store(response)
    return user


def _session_response(
    user: User,
    pair: TokenPair,
    csrf_token: str,
) -> AuthSessionResponse:
    return AuthSessionResponse(
        access_token=pair.access_token,
        expires_in=pair.expires_in,
        csrf_token=csrf_token,
        user=CurrentUserResponse.model_validate(user),
    )


def _session_response_item(
    session: RefreshTokenSession,
    current_family_id: UUID | None,
) -> SessionResponse:
    return SessionResponse(
        family_id=session.family_id,
        created_at=session.created_at,
        last_used_at=session.last_used_at,
        expires_at=session.expires_at,
        is_persistent=session.is_persistent,
        user_agent=session.user_agent,
        ip_address=session.ip_address,
        current=session.family_id == current_family_id,
    )


def _current_family_id(request: Request) -> UUID | None:
    authorization = request.headers.get("authorization")
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    from app.auth.exceptions import InvalidTokenError
    from app.auth.tokens import JWTService

    try:
        return JWTService().decode_token(
            authorization[7:], expected_type="access"
        ).session_id
    except InvalidTokenError:
        return None


def _set_refresh_cookie(response: Response, pair: TokenPair) -> None:
    settings = get_settings()
    max_age = None
    if pair.is_persistent:
        max_age = max(
            1,
            int((pair.refresh_expires_at - datetime.now(UTC)).total_seconds()),
        )
    response.set_cookie(
        key=settings.auth_refresh_cookie_name,
        value=pair.refresh_token,
        max_age=max_age,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
        domain=settings.auth_cookie_domain,
        path="/api/v1/auth",
    )


def _set_csrf_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=settings.auth_csrf_cookie_name,
        value=token,
        httponly=False,
        secure=settings.auth_cookie_secure,
        samesite=settings.auth_cookie_samesite,
        domain=settings.auth_cookie_domain,
        path="/",
    )


def _clear_auth_cookies(response: Response) -> None:
    settings = get_settings()
    response.delete_cookie(
        settings.auth_refresh_cookie_name,
        domain=settings.auth_cookie_domain,
        path="/api/v1/auth",
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite=settings.auth_cookie_samesite,
    )
    response.delete_cookie(
        settings.auth_csrf_cookie_name,
        domain=settings.auth_cookie_domain,
        path="/",
        secure=settings.auth_cookie_secure,
        httponly=False,
        samesite=settings.auth_cookie_samesite,
    )


def _set_no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"
