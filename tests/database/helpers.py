from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from tempfile import TemporaryDirectory

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.database.models import Base


@asynccontextmanager
async def sqlite_session() -> AsyncIterator[AsyncSession]:
    with TemporaryDirectory(prefix="arima-test-") as directory:
        engine: AsyncEngine = create_async_engine(
            f"sqlite+aiosqlite:///{directory}/test.sqlite3"
        )
        session_factory = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        try:
            async with session_factory() as session:
                yield session
        finally:
            await engine.dispose()
