from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Project, ProjectStatus
from app.database.repositories.base import AsyncRepository
from app.database.repositories.pagination import Page, escape_like, paginate
from app.schemas.common import SortDirection
from app.schemas.project import ProjectSortField


@dataclass(frozen=True, slots=True)
class ProjectFilters:
    status: ProjectStatus | None = None
    owner_id: UUID | None = None
    created_by: UUID | None = None
    search: str | None = None


class ProjectRepository(AsyncRepository[Project]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Project, session)

    async def list_by_owner(self, owner_id: UUID) -> Sequence[Project]:
        result = await self.session.scalars(
            select(Project).where(Project.owner_id == owner_id)
        )
        return result.all()

    async def get_for_update(self, project_id: UUID) -> Project | None:
        return await self.session.scalar(
            select(Project)
            .where(Project.id == project_id)
            .with_for_update()
        )

    async def get_owned(
        self,
        project_id: UUID,
        owner_id: UUID,
    ) -> Project | None:
        return await self.session.scalar(
            select(Project).where(
                Project.id == project_id,
                Project.owner_id == owner_id,
            )
        )

    async def get_owned_for_update(
        self,
        project_id: UUID,
        owner_id: UUID,
    ) -> Project | None:
        return await self.session.scalar(
            select(Project)
            .where(
                Project.id == project_id,
                Project.owner_id == owner_id,
            )
            .with_for_update()
        )

    async def get_many_for_update(
        self,
        project_ids: set[UUID],
    ) -> list[Project]:
        result = await self.session.scalars(
            select(Project)
            .where(Project.id.in_(project_ids))
            .order_by(Project.id)
            .with_for_update()
        )
        return list(result.all())

    async def list_filtered(
        self,
        filters: ProjectFilters,
        *,
        limit: int,
        offset: int,
        sort_by: ProjectSortField,
        direction: SortDirection,
    ) -> Page[Project]:
        statement = select(Project)
        if filters.status is not None:
            statement = statement.where(Project.status == filters.status)
        if filters.owner_id is not None:
            statement = statement.where(
                Project.owner_id == filters.owner_id
            )
        if filters.created_by is not None:
            statement = statement.where(
                Project.created_by == filters.created_by
            )
        if filters.search:
            pattern = f"%{escape_like(filters.search.strip())}%"
            statement = statement.where(
                or_(
                    Project.name.ilike(pattern, escape="\\"),
                    Project.description.ilike(pattern, escape="\\"),
                )
            )
        statement = self._apply_sort(statement, sort_by, direction)
        return await paginate(
            self.session,
            statement,
            limit=limit,
            offset=offset,
        )

    async def active_name_exists(
        self,
        *,
        owner_id: UUID,
        name: str,
        exclude_id: UUID | None = None,
    ) -> bool:
        statement = select(Project.id).where(
            Project.owner_id == owner_id,
            func.lower(Project.name) == name.lower(),
            Project.archived_at.is_(None),
        )
        if exclude_id is not None:
            statement = statement.where(Project.id != exclude_id)
        return await self.session.scalar(statement) is not None

    @staticmethod
    def _apply_sort(
        statement: Select[tuple[Project]],
        sort_by: ProjectSortField,
        direction: SortDirection,
    ) -> Select[tuple[Project]]:
        columns = {
            ProjectSortField.NAME: Project.name,
            ProjectSortField.CREATED_AT: Project.created_at,
            ProjectSortField.UPDATED_AT: Project.updated_at,
        }
        column = columns[sort_by]
        ordering = (
            column.asc()
            if direction is SortDirection.ASC
            else column.desc()
        )
        id_ordering = (
            Project.id.asc()
            if direction is SortDirection.ASC
            else Project.id.desc()
        )
        return statement.order_by(ordering, id_ordering)
