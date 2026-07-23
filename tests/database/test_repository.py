import asyncio

from app.database.models import Role
from app.database.repositories import AsyncRepository
from app.database.session import engine as production_engine
from tests.database.helpers import sqlite_session


def test_production_engine_uses_asyncpg() -> None:
    assert production_engine.url.drivername == "postgresql+asyncpg"


def test_async_repository_with_sqlite() -> None:
    async def exercise_repository() -> None:
        async with sqlite_session() as session:
            repository = AsyncRepository(Role, session)
            role = await repository.add(Role(name="example"))

            assert role.id is not None
            assert await repository.get(role.id) is role
            assert list(await repository.list()) == [role]

            await repository.delete(role)
            assert await repository.get(role.id) is None

    asyncio.run(exercise_repository())
