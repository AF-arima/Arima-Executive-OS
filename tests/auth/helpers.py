import asyncio
import re
from urllib.parse import unquote

from app.auth.roles import seed_default_roles
from app.database.repositories import UserRepository
from tests.auth.conftest import AuthTestContext

VALID_PASSWORD = "StrongPassword1!"


def registration_payload(
    email: str = "user@example.com",
) -> dict[str, str]:
    return {
        "email": email,
        "password": VALID_PASSWORD,
        "first_name": "Test",
        "last_name": "User",
    }


def register_user(
    context: AuthTestContext,
    email: str = "user@example.com",
    *,
    verify: bool = True,
) -> dict[str, object]:
    response = context.client.post(
        "/api/v1/auth/register",
        json=registration_payload(email),
        headers=csrf_headers(context),
    )
    assert response.status_code == 201
    if verify:
        set_user_verified(context, email, is_verified=True)
    body = response.json()
    user = body["user"]
    assert isinstance(user, dict)
    return user


def login_user(
    context: AuthTestContext,
    email: str = "user@example.com",
    password: str = VALID_PASSWORD,
) -> dict[str, object]:
    response = context.client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
        headers=csrf_headers(context),
    )
    assert response.status_code == 200
    return response.json()


def bearer(token: object) -> dict[str, str]:
    assert isinstance(token, str)
    return {"Authorization": f"Bearer {token}"}


def csrf_headers(context: AuthTestContext) -> dict[str, str]:
    response = context.client.post("/api/v1/auth/csrf")
    assert response.status_code == 200
    token = response.json()["csrf_token"]
    assert isinstance(token, str)
    return {"X-CSRF-Token": token}


def email_token(context: AuthTestContext, subject_fragment: str) -> str:
    message = next(
        message
        for message in reversed(context.email_provider.messages)
        if subject_fragment.lower() in message.subject.lower()
    )
    match = re.search(r"[?&]token=([^&\s]+)", message.text_body)
    assert match is not None
    return unquote(match.group(1))


def set_user_active(
    context: AuthTestContext,
    email: str,
    *,
    is_active: bool,
) -> None:
    async def update_user() -> None:
        async with context.session_factory() as session:
            user = await UserRepository(session).get_by_email(email)
            assert user is not None
            user.is_active = is_active
            await session.commit()

    asyncio.run(update_user())


def set_user_verified(
    context: AuthTestContext,
    email: str,
    *,
    is_verified: bool,
) -> None:
    async def update_user() -> None:
        async with context.session_factory() as session:
            user = await UserRepository(session).get_by_email(email)
            assert user is not None
            user.is_verified = is_verified
            await session.commit()

    asyncio.run(update_user())


def grant_role(
    context: AuthTestContext,
    email: str,
    role_name: str,
) -> None:
    async def update_roles() -> None:
        async with context.session_factory() as session:
            roles = await seed_default_roles(session)
            user = await UserRepository(session).get_by_email(email)
            assert user is not None
            user.roles = [roles[role_name]]
            await session.commit()

    asyncio.run(update_roles())
