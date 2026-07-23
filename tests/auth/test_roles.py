import asyncio

from sqlalchemy import func, select

from app.auth.roles import DEFAULT_ROLES, seed_default_roles
from app.database.models import Role
from tests.database.helpers import sqlite_session


def test_default_role_seeding_is_idempotent() -> None:
    async def seed_twice() -> None:
        async with sqlite_session() as session:
            first = await seed_default_roles(session)
            second = await seed_default_roles(session)
            await session.commit()

            count = await session.scalar(
                select(func.count()).select_from(Role)
            )

            assert set(first) == set(DEFAULT_ROLES)
            assert set(second) == set(DEFAULT_ROLES)
            assert count == len(DEFAULT_ROLES)

    asyncio.run(seed_twice())
