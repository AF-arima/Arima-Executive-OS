from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import Select, case, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Task, TaskPriority, TaskStatus
from app.database.repositories.base import AsyncRepository
from app.database.repositories.pagination import Page, escape_like, paginate
from app.schemas.common import SortDirection
from app.schemas.task import TaskSortField


@dataclass(frozen=True, slots=True)
class TaskFilters:
    status: TaskStatus | None = None
    priority: TaskPriority | None = None
    project_id: UUID | None = None
    assigned_to: UUID | None = None
    overdue: bool | None = None
    completed: bool | None = None
    search: str | None = None


class TaskRepository(AsyncRepository[Task]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Task, session)

    async def list_by_project(
        self,
        project_id: UUID,
    ) -> Sequence[Task]:
        result = await self.session.scalars(
            select(Task).where(Task.project_id == project_id)
        )
        return result.all()

    async def get_for_update(self, task_id: UUID) -> Task | None:
        return await self.session.scalar(
            select(Task).where(Task.id == task_id).with_for_update()
        )

    async def list_by_assignee(
        self,
        assignee_id: UUID,
    ) -> Sequence[Task]:
        result = await self.session.scalars(
            select(Task).where(Task.assignee_id == assignee_id)
        )
        return result.all()

    async def list_filtered(
        self,
        filters: TaskFilters,
        *,
        now: datetime,
        limit: int,
        offset: int,
        sort_by: TaskSortField,
        direction: SortDirection,
    ) -> Page[Task]:
        statement = select(Task)
        if filters.status is not None:
            statement = statement.where(Task.status == filters.status)
        if filters.priority is not None:
            statement = statement.where(Task.priority == filters.priority)
        if filters.project_id is not None:
            statement = statement.where(
                Task.project_id == filters.project_id
            )
        if filters.assigned_to is not None:
            statement = statement.where(
                Task.assignee_id == filters.assigned_to
            )
        if filters.overdue is True:
            statement = statement.where(
                Task.due_date < now,
                Task.completed_at.is_(None),
            )
        elif filters.overdue is False:
            statement = statement.where(
                or_(
                    Task.due_date.is_(None),
                    Task.due_date >= now,
                    Task.completed_at.is_not(None),
                )
            )
        if filters.completed is True:
            statement = statement.where(Task.completed_at.is_not(None))
        elif filters.completed is False:
            statement = statement.where(Task.completed_at.is_(None))
        if filters.search:
            pattern = f"%{escape_like(filters.search.strip())}%"
            statement = statement.where(
                or_(
                    Task.title.ilike(pattern, escape="\\"),
                    Task.description.ilike(pattern, escape="\\"),
                )
            )
        statement = self._apply_sort(statement, sort_by, direction)
        return await paginate(
            self.session,
            statement,
            limit=limit,
            offset=offset,
        )

    @staticmethod
    def _apply_sort(
        statement: Select[tuple[Task]],
        sort_by: TaskSortField,
        direction: SortDirection,
    ) -> Select[tuple[Task]]:
        priority_order = case(
            (Task.priority == TaskPriority.LOW, 1),
            (Task.priority == TaskPriority.MEDIUM, 2),
            (Task.priority == TaskPriority.HIGH, 3),
            (Task.priority == TaskPriority.URGENT, 4),
            else_=0,
        )
        columns = {
            TaskSortField.CREATED_AT: Task.created_at,
            TaskSortField.UPDATED_AT: Task.updated_at,
            TaskSortField.DUE_DATE: Task.due_date,
            TaskSortField.PRIORITY: priority_order,
        }
        column = columns[sort_by]
        ordering = (
            column.asc().nulls_last()
            if direction is SortDirection.ASC
            else column.desc().nulls_last()
        )
        id_ordering = (
            Task.id.asc()
            if direction is SortDirection.ASC
            else Task.id.desc()
        )
        return statement.order_by(ordering, id_ordering)
