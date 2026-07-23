import asyncio

from sqlalchemy import select

from app.database.models import RefreshTokenSession
from tests.auth.conftest import AuthTestContext
from tests.auth.helpers import (
    bearer,
    login_user,
    register_user,
    set_user_active,
)


def refresh_sessions(
    context: AuthTestContext,
) -> list[RefreshTokenSession]:
    async def load_sessions() -> list[RefreshTokenSession]:
        async with context.session_factory() as session:
            result = await session.scalars(select(RefreshTokenSession))
            return list(result.all())

    return asyncio.run(load_sessions())


def test_authenticated_current_user_has_default_viewer_role(
    auth_context: AuthTestContext,
) -> None:
    registered = register_user(auth_context)
    tokens = login_user(auth_context)

    response = auth_context.client.get(
        "/api/v1/auth/me",
        headers=bearer(tokens["access_token"]),
    )

    assert response.status_code == 200
    assert response.json()["id"] == registered["id"]
    assert [role["name"] for role in response.json()["roles"]] == ["viewer"]
    assert "hashed_password" not in response.json()
    assert response.headers["cache-control"] == "no-store"


def test_current_user_rejects_missing_invalid_and_refresh_tokens(
    auth_context: AuthTestContext,
) -> None:
    register_user(auth_context)
    tokens = login_user(auth_context)

    missing = auth_context.client.get("/api/v1/auth/me")
    invalid = auth_context.client.get(
        "/api/v1/auth/me",
        headers=bearer("malformed-token"),
    )
    refresh = auth_context.client.get(
        "/api/v1/auth/me",
        headers=bearer(tokens["refresh_token"]),
    )

    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert refresh.status_code == 401


def test_refresh_session_stores_only_safe_identifier(
    auth_context: AuthTestContext,
) -> None:
    register_user(auth_context)
    tokens = login_user(auth_context)

    sessions = refresh_sessions(auth_context)

    assert len(sessions) == 1
    assert len(sessions[0].token_jti) == 36
    assert sessions[0].token_jti != tokens["refresh_token"]
    assert "refresh_token" not in RefreshTokenSession.__table__.columns


def test_inactive_current_user_is_rejected(
    auth_context: AuthTestContext,
) -> None:
    register_user(auth_context)
    tokens = login_user(auth_context)
    set_user_active(
        auth_context,
        "user@example.com",
        is_active=False,
    )

    response = auth_context.client.get(
        "/api/v1/auth/me",
        headers=bearer(tokens["access_token"]),
    )

    assert response.status_code == 403


def test_refresh_rotation_and_reuse_detection(
    auth_context: AuthTestContext,
) -> None:
    register_user(auth_context)
    original = login_user(auth_context)

    rotated = auth_context.client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": original["refresh_token"]},
    )
    reused = auth_context.client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": original["refresh_token"]},
    )

    assert rotated.status_code == 200
    assert rotated.json()["refresh_token"] != original["refresh_token"]
    assert rotated.headers["cache-control"] == "no-store"
    assert rotated.headers["pragma"] == "no-cache"
    assert reused.status_code == 401

    accepted = auth_context.client.get(
        "/api/v1/auth/me",
        headers=bearer(rotated.json()["access_token"]),
    )
    assert accepted.status_code == 200


def test_logout_revokes_refresh_session(
    auth_context: AuthTestContext,
) -> None:
    register_user(auth_context)
    tokens = login_user(auth_context)

    logout = auth_context.client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": tokens["refresh_token"]},
    )
    refresh = auth_context.client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": tokens["refresh_token"]},
    )

    assert logout.status_code == 204
    assert logout.content == b""
    assert refresh.status_code == 401
