import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import Response
from sqlalchemy import select

from app.api.v1.routes import auth as auth_routes
from app.core.config import Settings
from app.database.models import RefreshTokenSession, User, Workspace
from tests.auth.conftest import AuthTestContext
from tests.auth.helpers import (
    VALID_PASSWORD,
    bearer,
    csrf_headers,
    email_token,
    login_user,
    register_user,
)


def refresh_cookie(context: AuthTestContext) -> str:
    value = context.client.cookies.get("arima_refresh_token")
    assert isinstance(value, str)
    return value


def test_me_requires_active_access_session(
    auth_context: AuthTestContext,
) -> None:
    registered = register_user(auth_context)
    session = login_user(auth_context)

    response = auth_context.client.get(
        "/api/v1/auth/me",
        headers=bearer(session["access_token"]),
    )

    assert response.status_code == 200
    assert response.json()["id"] == str(registered["id"])
    assert [role["name"] for role in response.json()["roles"]] == ["manager"]
    assert response.json()["workspace"]["owner_id"] == str(registered["id"])
    assert "hashed_password" not in response.json()


def test_refresh_rotates_cookie_and_detects_reuse(
    auth_context: AuthTestContext,
) -> None:
    register_user(auth_context)
    original = login_user(auth_context)
    original_refresh = refresh_cookie(auth_context)

    rotated = auth_context.client.post(
        "/api/v1/auth/refresh",
        headers=csrf_headers(auth_context),
    )
    assert rotated.status_code == 200
    assert rotated.json()["access_token"] != original["access_token"]
    assert refresh_cookie(auth_context) != original_refresh

    auth_context.client.cookies.set("arima_refresh_token", original_refresh)
    reused = auth_context.client.post(
        "/api/v1/auth/refresh",
        headers=csrf_headers(auth_context),
    )
    assert reused.status_code == 401

    revoked_access = auth_context.client.get(
        "/api/v1/auth/me",
        headers=bearer(rotated.json()["access_token"]),
    )
    assert revoked_access.status_code == 401


def test_logout_revokes_access_and_refresh_session(
    auth_context: AuthTestContext,
) -> None:
    register_user(auth_context)
    session = login_user(auth_context)

    logout = auth_context.client.post(
        "/api/v1/auth/logout",
        headers=csrf_headers(auth_context),
    )
    assert logout.status_code == 204
    assert auth_context.client.cookies.get("arima_refresh_token") is None

    current = auth_context.client.get(
        "/api/v1/auth/me",
        headers=bearer(session["access_token"]),
    )
    assert current.status_code == 401


def test_logout_is_idempotent_for_an_invalid_refresh_cookie(
    auth_context: AuthTestContext,
) -> None:
    auth_context.client.cookies.set(
        "arima_refresh_token",
        "invalid",
        path="/api/v1/auth",
    )

    response = auth_context.client.post(
        "/api/v1/auth/logout",
        headers=csrf_headers(auth_context),
    )

    assert response.status_code == 204
    assert any(
        "arima_refresh_token" in value.lower()
        and "max-age=0" in value.lower()
        for value in response.headers.get_list("set-cookie")
    )


def test_logout_cookie_deletion_matches_production_cookie_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        auth_cookie_secure=True,
        auth_cookie_samesite="none",
    )
    monkeypatch.setattr(auth_routes, "get_settings", lambda: settings)

    response = Response()
    auth_routes._clear_auth_cookies(response)
    headers = response.headers.getlist("set-cookie")

    refresh = next(
        value
        for value in headers
        if value.startswith(f"{settings.auth_refresh_cookie_name}=")
    ).lower()
    csrf = next(
        value
        for value in headers
        if value.startswith(f"{settings.auth_csrf_cookie_name}=")
    ).lower()

    assert "max-age=0" in refresh
    assert "path=/api/v1/auth" in refresh
    assert "secure" in refresh
    assert "httponly" in refresh
    assert "samesite=none" in refresh

    assert "max-age=0" in csrf
    assert "path=/" in csrf
    assert "secure" in csrf
    assert "httponly" not in csrf
    assert "samesite=none" in csrf


def test_password_reset_revokes_existing_sessions(
    auth_context: AuthTestContext,
) -> None:
    register_user(auth_context)
    original = login_user(auth_context)

    requested = auth_context.client.post(
        "/api/v1/auth/forgot-password",
        json={"email": "user@example.com"},
        headers=csrf_headers(auth_context),
    )
    assert requested.status_code == 202
    token = email_token(auth_context, "reset")
    reset = auth_context.client.post(
        "/api/v1/auth/reset-password",
        json={"token": token, "password": "AnotherStrongPassword1!"},
        headers=csrf_headers(auth_context),
    )
    assert reset.status_code == 204

    old_access = auth_context.client.get(
        "/api/v1/auth/me",
        headers=bearer(original["access_token"]),
    )
    assert old_access.status_code == 401
    new_login = login_user(
        auth_context,
        password="AnotherStrongPassword1!",
    )
    assert isinstance(new_login["access_token"], str)


def test_sessions_can_be_listed_and_revoked_together(
    auth_context: AuthTestContext,
) -> None:
    register_user(auth_context)
    session = login_user(auth_context, password=VALID_PASSWORD)
    headers = bearer(session["access_token"])

    listed = auth_context.client.get("/api/v1/auth/sessions", headers=headers)
    assert listed.status_code == 200
    assert len(listed.json()["items"]) == 1
    assert listed.json()["items"][0]["current"] is True

    logout_all = auth_context.client.post(
        "/api/v1/auth/logout-all",
        headers={**headers, **csrf_headers(auth_context)},
    )
    assert logout_all.status_code == 204
    assert auth_context.client.get("/api/v1/auth/me", headers=headers).status_code == 401


def test_account_lockout_is_enforced_after_repeated_failures(
    auth_context: AuthTestContext,
) -> None:
    register_user(auth_context)
    statuses = []
    for _ in range(5):
        response = auth_context.client.post(
            "/api/v1/auth/login",
            json={"email": "user@example.com", "password": "WrongPassword1!"},
            headers=csrf_headers(auth_context),
        )
        statuses.append(response.status_code)

    locked = auth_context.client.post(
        "/api/v1/auth/login",
        json={"email": "user@example.com", "password": VALID_PASSWORD},
        headers=csrf_headers(auth_context),
    )
    assert statuses[:4] == [401, 401, 401, 401]
    assert statuses[4] == 423
    assert locked.status_code == 423
    assert "retry-after" in locked.headers


def test_expired_lockout_resets_before_the_next_failed_attempt(
    auth_context: AuthTestContext,
) -> None:
    register_user(auth_context)

    async def expire_lockout() -> None:
        async with auth_context.session_factory() as session:
            user = await session.scalar(
                select(User).where(User.email == "user@example.com")
            )
            assert user is not None
            user.failed_login_attempts = 5
            user.locked_until = datetime.now(UTC) - timedelta(seconds=1)
            await session.commit()

    asyncio.run(expire_lockout())
    response = auth_context.client.post(
        "/api/v1/auth/login",
        json={"email": "user@example.com", "password": "WrongPassword1!"},
        headers=csrf_headers(auth_context),
    )
    assert response.status_code == 401

    async def verify_reset() -> None:
        async with auth_context.session_factory() as session:
            user = await session.scalar(
                select(User).where(User.email == "user@example.com")
            )
            assert user is not None
            assert user.failed_login_attempts == 1
            assert user.locked_until is None

    asyncio.run(verify_reset())


def test_refresh_table_never_stores_raw_token(
    auth_context: AuthTestContext,
) -> None:
    register_user(auth_context)
    login_user(auth_context)

    async def load_sessions() -> list[RefreshTokenSession]:
        async with auth_context.session_factory() as session:
            return list((await session.scalars(select(RefreshTokenSession))).all())

    sessions = asyncio.run(load_sessions())
    assert len(sessions) == 1
    assert len(sessions[0].token_jti) == 36
    assert "refresh_token" not in RefreshTokenSession.__table__.columns


def test_profile_password_and_email_change_revoke_existing_sessions(
    auth_context: AuthTestContext,
) -> None:
    register_user(auth_context)
    original = login_user(auth_context)
    headers = {
        **bearer(original["access_token"]),
        **csrf_headers(auth_context),
    }

    profile = auth_context.client.patch(
        "/api/v1/auth/me",
        headers=headers,
        json={"first_name": "Updated", "last_name": "Executive"},
    )
    assert profile.status_code == 200
    assert profile.json()["first_name"] == "Updated"
    assert profile.json()["workspace"] is not None

    changed_password = auth_context.client.post(
        "/api/v1/auth/change-password",
        headers=headers,
        json={
            "current_password": VALID_PASSWORD,
            "password": "DifferentStrongPassword1!",
        },
    )
    assert changed_password.status_code == 204
    assert (
        auth_context.client.get(
            "/api/v1/auth/me",
            headers=bearer(original["access_token"]),
        ).status_code
        == 401
    )

    session = login_user(
        auth_context,
        password="DifferentStrongPassword1!",
    )
    change_headers = {
        **bearer(session["access_token"]),
        **csrf_headers(auth_context),
    }
    requested = auth_context.client.post(
        "/api/v1/auth/change-email",
        headers=change_headers,
        json={
            "new_email": "new-address@example.com",
            "current_password": "DifferentStrongPassword1!",
        },
    )
    assert requested.status_code == 202
    token = email_token(auth_context, "confirm your new")
    confirmed = auth_context.client.post(
        "/api/v1/auth/change-email/confirm",
        headers=csrf_headers(auth_context),
        json={"token": token},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["email"] == "new-address@example.com"
    assert (
        auth_context.client.get(
            "/api/v1/auth/me",
            headers=bearer(session["access_token"]),
        ).status_code
        == 401
    )
    assert auth_context.email_provider.messages[-1].to_address == "user@example.com"
    assert isinstance(
        login_user(
            auth_context,
            email="new-address@example.com",
            password="DifferentStrongPassword1!",
        )["access_token"],
        str,
    )


def test_required_email_delivery_failure_rolls_back_registration(
    auth_context: AuthTestContext,
) -> None:
    auth_context.email_provider.fail_next_delivery = True
    failed = auth_context.client.post(
        "/api/v1/auth/register",
        json={
            "email": "delivery@example.com",
            "password": VALID_PASSWORD,
            "first_name": "Delivery",
            "last_name": "Failure",
        },
        headers=csrf_headers(auth_context),
    )
    assert failed.status_code == 503

    retried = auth_context.client.post(
        "/api/v1/auth/register",
        json={
            "email": "delivery@example.com",
            "password": VALID_PASSWORD,
            "first_name": "Delivery",
            "last_name": "Failure",
        },
        headers=csrf_headers(auth_context),
    )
    assert retried.status_code == 201


def test_long_names_create_a_workspace_within_the_database_limit(
    auth_context: AuthTestContext,
) -> None:
    response = auth_context.client.post(
        "/api/v1/auth/register",
        json={
            "email": "long-name@example.com",
            "password": VALID_PASSWORD,
            "first_name": "F" * 100,
            "last_name": "L" * 100,
        },
        headers=csrf_headers(auth_context),
    )
    assert response.status_code == 201

    async def workspace_name() -> str:
        async with auth_context.session_factory() as session:
            workspace = await session.scalar(select(Workspace))
            assert workspace is not None
            return workspace.name

    assert len(asyncio.run(workspace_name())) <= 160
