from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

settings = get_settings()


def _engine_options(database_url: str) -> dict[str, Any]:
    """Return pooling options supported by the selected database dialect."""
    if database_url.startswith("sqlite"):
        # SQLite's async dialect uses a StaticPool for in-memory databases and
        # does not accept QueuePool sizing arguments.
        return {}

    return {
        "pool_size": settings.database_pool_size,
        "max_overflow": settings.database_max_overflow,
        "pool_recycle": settings.database_pool_recycle_seconds,
        "pool_timeout": settings.database_pool_timeout_seconds,
        "pool_pre_ping": True,
        "connect_args": {
            "timeout": settings.database_connect_timeout_seconds,
        },
    }


engine: AsyncEngine = create_async_engine(
    settings.database_url,
    **_engine_options(settings.database_url),
)
async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with async_session_factory() as session:
        yield session
