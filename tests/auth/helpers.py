import asyncio

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
) -> dict[str, object]:
    response = context.client.post(
        "/api/v1/auth/register",
        json=registration_payload(email),
    )
    assert response.status_code == 201
    return response.json()


def login_user(
    context: AuthTestContext,
    email: str = "user@example.com",
    password: str = VALID_PASSWORD,
) -> dict[str, object]:
    response = context.client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert response.status_code == 200
    return response.json()


def bearer(token: object) -> dict[str, str]:
    assert isinstance(token, str)
    return {"Authorization": f"Bearer {token}"}


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
            if all(role.name != role_name for role in user.roles):
                user.roles.append(roles[role_name])
            await session.commit()

    asyncio.run(update_roles())
