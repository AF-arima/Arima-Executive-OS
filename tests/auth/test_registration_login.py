import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.auth.service import AuthenticationService
from app.database.models import Workspace, WorkspaceMembership
from app.email.service import TransactionalEmailService
from app.schemas.auth import UserRegistration
from tests.auth.conftest import AuthTestContext
from tests.auth.helpers import (
    VALID_PASSWORD,
    csrf_headers,
    email_token,
    register_user,
    registration_payload,
    set_user_active,
)


def test_registration_creates_pending_verified_workspace(
    auth_context: AuthTestContext,
) -> None:
    response = auth_context.client.post(
        "/api/v1/auth/register",
        json=registration_payload("Normal.User@EXAMPLE.COM"),
        headers=csrf_headers(auth_context),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["verification_required"] is True
    assert body["user"]["email"] == "normal.user@example.com"
    assert body["user"]["is_verified"] is False
    assert "hashed_password" not in response.text
    assert VALID_PASSWORD not in response.text
    assert auth_context.email_provider.messages[-1].to_address == (
        "normal.user@example.com"
    )

    async def assert_workspace() -> None:
        async with auth_context.session_factory() as session:
            workspaces = list((await session.scalars(select(Workspace))).all())
            memberships = list(
                (await session.scalars(select(WorkspaceMembership))).all()
            )
            assert len(workspaces) == 1
            assert len(memberships) == 1
            assert workspaces[0].owner_id == memberships[0].user_id
            assert memberships[0].role == "owner"

    asyncio.run(assert_workspace())


def test_unverified_user_must_verify_before_login(
    auth_context: AuthTestContext,
) -> None:
    register_user(auth_context, verify=False)

    response = auth_context.client.post(
        "/api/v1/auth/login",
        json={"email": "user@example.com", "password": VALID_PASSWORD},
        headers=csrf_headers(auth_context),
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Email verification is required"}


def test_verify_then_login_sets_secure_session_contract(
    auth_context: AuthTestContext,
) -> None:
    register_user(auth_context, verify=False)
    token = email_token(auth_context, "verify")
    verified = auth_context.client.post(
        "/api/v1/auth/verify-email",
        json={"token": token},
        headers=csrf_headers(auth_context),
    )
    assert verified.status_code == 200
    assert verified.json()["is_verified"] is True

    login_response = auth_context.client.post(
        "/api/v1/auth/login",
        json={"email": "user@example.com", "password": VALID_PASSWORD},
        headers=csrf_headers(auth_context),
    )
    assert login_response.status_code == 200
    body = login_response.json()
    assert body["token_type"] == "bearer"
    assert isinstance(body["access_token"], str)
    assert "refresh_token" not in body
    assert isinstance(body["csrf_token"], str)
    assert body["user"]["is_verified"] is True
    assert "httponly" in login_response.headers["set-cookie"].lower()
    assert auth_context.client.cookies.get("arima_refresh_token")


def test_duplicate_and_invalid_registration_are_rejected(
    auth_context: AuthTestContext,
) -> None:
    register_user(auth_context, "duplicate@example.com")
    duplicate = auth_context.client.post(
        "/api/v1/auth/register",
        json=registration_payload("DUPLICATE@example.com"),
        headers=csrf_headers(auth_context),
    )
    invalid = auth_context.client.post(
        "/api/v1/auth/register",
        json=registration_payload("invalid-email"),
        headers=csrf_headers(auth_context),
    )
    weak = auth_context.client.post(
        "/api/v1/auth/register",
        json={**registration_payload("new@example.com"), "password": "weak"},
        headers=csrf_headers(auth_context),
    )

    assert duplicate.status_code == 409
    assert invalid.status_code == 422
    assert weak.status_code == 422
    assert "weak" not in weak.text


def test_registration_does_not_hide_unrelated_integrity_errors(
    auth_context: AuthTestContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_token_creation(*_: object, **__: object) -> str:
        raise IntegrityError(
            "INSERT",
            {},
            RuntimeError("unrelated persistence error"),
        )

    async def exercise() -> None:
        async with auth_context.session_factory() as session:
            service = AuthenticationService(
                session,
                email_service=TransactionalEmailService(
                    auth_context.email_provider
                ),
            )
            monkeypatch.setattr(service, "_issue_security_token", fail_token_creation)

            with pytest.raises(IntegrityError, match="unrelated persistence error"):
                await service.register_user(
                    UserRegistration(
                        **registration_payload("persistence-error@example.com")
                    )
                )

    asyncio.run(exercise())


def test_login_rejects_generic_invalid_credentials_and_inactive_users(
    auth_context: AuthTestContext,
) -> None:
    register_user(auth_context)
    wrong = auth_context.client.post(
        "/api/v1/auth/login",
        json={"email": "user@example.com", "password": "WrongPassword1!"},
        headers=csrf_headers(auth_context),
    )
    unknown = auth_context.client.post(
        "/api/v1/auth/login",
        json={"email": "unknown@example.com", "password": VALID_PASSWORD},
        headers=csrf_headers(auth_context),
    )
    set_user_active(auth_context, "user@example.com", is_active=False)
    inactive = auth_context.client.post(
        "/api/v1/auth/login",
        json={"email": "user@example.com", "password": VALID_PASSWORD},
        headers=csrf_headers(auth_context),
    )

    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json() == {
        "detail": "Invalid email or password"
    }
    assert inactive.status_code == 403


def test_csrf_is_required_for_authentication_state_changes(
    auth_context: AuthTestContext,
) -> None:
    response = auth_context.client.post(
        "/api/v1/auth/register",
        json=registration_payload(),
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "CSRF validation failed"}
