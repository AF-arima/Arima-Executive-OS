from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Task
from app.database.repositories.base import AsyncRepository


class TaskRepository(AsyncRepository[Task]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Task, session)

    async def list_by_project(self, project_id: UUID) -> Sequence[Task]:
        result = await self.session.scalars(
            select(Task).where(Task.project_id == project_id)
        )
        return result.all()

    async def list_by_assignee(self, assignee_id: UUID) -> Sequence[Task]:
        result = await self.session.scalars(
            select(Task).where(Task.assignee_id == assignee_id)
        )
        return result.all()
