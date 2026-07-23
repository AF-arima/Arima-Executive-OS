from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Project
from app.database.repositories.base import AsyncRepository


class ProjectRepository(AsyncRepository[Project]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Project, session)

    async def list_by_owner(self, owner_id: UUID) -> Sequence[Project]:
        result = await self.session.scalars(
            select(Project).where(Project.owner_id == owner_id)
        )
        return result.all()
