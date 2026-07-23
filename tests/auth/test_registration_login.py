from tests.auth.conftest import AuthTestContext
from tests.auth.helpers import (
    VALID_PASSWORD,
    login_user,
    register_user,
    registration_payload,
    set_user_active,
)


def test_successful_registration_normalizes_email_and_hides_hash(
    auth_context: AuthTestContext,
) -> None:
    response = auth_context.client.post(
        "/api/v1/auth/register",
        json=registration_payload("Normal.User@EXAMPLE.COM"),
    )

    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "normal.user@example.com"
    assert "hashed_password" not in body
    assert "password" not in body
    assert VALID_PASSWORD not in response.text


def test_duplicate_email_is_rejected(
    auth_context: AuthTestContext,
) -> None:
    register_user(auth_context, "duplicate@example.com")

    response = auth_context.client.post(
        "/api/v1/auth/register",
        json=registration_payload("DUPLICATE@example.com"),
    )

    assert response.status_code == 409
    assert response.json() == {"detail": "Email is already registered"}


def test_invalid_email_is_rejected(
    auth_context: AuthTestContext,
) -> None:
    response = auth_context.client.post(
        "/api/v1/auth/register",
        json=registration_payload("invalid-email"),
    )

    assert response.status_code == 422


def test_weak_password_is_rejected_without_echoing_it(
    auth_context: AuthTestContext,
) -> None:
    payload = registration_payload()
    payload["password"] = "weak"

    response = auth_context.client.post(
        "/api/v1/auth/register",
        json=payload,
    )

    assert response.status_code == 422
    assert "weak" not in response.text


def test_unexpected_password_hash_field_is_rejected_and_redacted(
    auth_context: AuthTestContext,
) -> None:
    payload = registration_payload()
    payload["hashed_password"] = "sensitive-hash-value"

    response = auth_context.client.post(
        "/api/v1/auth/register",
        json=payload,
    )

    assert response.status_code == 422
    assert "sensitive-hash-value" not in response.text


def test_successful_login(auth_context: AuthTestContext) -> None:
    register_user(auth_context)

    body = login_user(auth_context)

    assert body["token_type"] == "bearer"
    assert isinstance(body["access_token"], str)
    assert isinstance(body["refresh_token"], str)
    assert body["access_token"] != body["refresh_token"]
    response = auth_context.client.post(
        "/api/v1/auth/login",
        json={
            "email": "user@example.com",
            "password": VALID_PASSWORD,
        },
    )
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["pragma"] == "no-cache"


def test_oauth2_form_login(auth_context: AuthTestContext) -> None:
    register_user(auth_context)

    response = auth_context.client.post(
        "/api/v1/auth/login",
        data={
            "username": "USER@example.com",
            "password": VALID_PASSWORD,
        },
    )

    assert response.status_code == 200
    assert response.json()["token_type"] == "bearer"


def test_wrong_password_and_unknown_email_are_generic(
    auth_context: AuthTestContext,
) -> None:
    register_user(auth_context)

    wrong = auth_context.client.post(
        "/api/v1/auth/login",
        json={
            "email": "user@example.com",
            "password": "WrongPassword1!",
        },
    )
    unknown = auth_context.client.post(
        "/api/v1/auth/login",
        json={
            "email": "unknown@example.com",
            "password": VALID_PASSWORD,
        },
    )

    assert wrong.status_code == 401
    assert unknown.status_code == 401
    assert wrong.json() == unknown.json() == {
        "detail": "Invalid email or password"
    }
    assert wrong.headers["www-authenticate"] == "Bearer"


def test_inactive_user_cannot_login(
    auth_context: AuthTestContext,
) -> None:
    register_user(auth_context)
    set_user_active(
        auth_context,
        "user@example.com",
        is_active=False,
    )

    response = auth_context.client.post(
        "/api/v1/auth/login",
        json={
            "email": "user@example.com",
            "password": VALID_PASSWORD,
        },
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Inactive user"}
