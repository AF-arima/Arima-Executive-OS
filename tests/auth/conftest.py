import asyncio
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.database.models import Base
from app.database.session import get_session
from app.main import app


@dataclass(frozen=True)
class AuthTestContext:
    client: TestClient
    session_factory: async_sessionmaker[AsyncSession]


@pytest.fixture
def auth_context(tmp_path: Path) -> Iterator[AuthTestContext]:
    database_path = tmp_path / "auth.sqlite3"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{database_path}"
    )
    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async def create_schema() -> None:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    asyncio.run(create_schema())
    app.dependency_overrides[get_session] = override_session
    try:
        with TestClient(app) as client:
            yield AuthTestContext(client, session_factory)
    finally:
        app.dependency_overrides.clear()
        asyncio.run(engine.dispose())
