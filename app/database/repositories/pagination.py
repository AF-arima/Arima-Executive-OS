from dataclasses import dataclass
from typing import Generic, TypeVar

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.base import Base

ModelT = TypeVar("ModelT", bound=Base)


@dataclass(frozen=True, slots=True)
class Page(Generic[ModelT]):
    items: list[ModelT]
    total: int
    limit: int
    offset: int


async def paginate(
    session: AsyncSession,
    statement: Select[tuple[ModelT]],
    *,
    limit: int,
    offset: int,
) -> Page[ModelT]:
    count_statement = select(func.count()).select_from(
        statement.order_by(None).subquery()
    )
    total = await session.scalar(count_statement)
    result = await session.scalars(
        statement.limit(limit).offset(offset)
    )
    return Page(
        items=list(result.all()),
        total=total or 0,
        limit=limit,
        offset=offset,
    )


def escape_like(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )
