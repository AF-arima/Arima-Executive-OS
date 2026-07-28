from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    AuditAction,
    AuditEntity,
    Project,
    User,
)
from app.database.repositories import (
    Page,
    ProjectFilters,
    ProjectRepository,
)
from app.schemas.common import SortDirection
from app.schemas.project import (
    ProjectCreate,
    ProjectSortField,
    ProjectUpdate,
)
from app.services.audit import record_audit
from app.services.cache import dashboard_cache
from app.services.exceptions import (
    PermissionDeniedError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from app.services.permissions import (
    can_create_project,
    can_manage_project,
)
from app.services.notification import enqueue_project_status_change


class ProjectService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.projects = ProjectRepository(session)

    async def create(self, data: ProjectCreate, actor: User) -> Project:
        if not can_create_project(actor):
            raise PermissionDeniedError

        owner_id = data.owner_id or actor.id
        if owner_id != actor.id:
            raise PermissionDeniedError
        if await self.projects.active_name_exists(
            owner_id=owner_id,
            name=data.name,
        ):
            raise ResourceConflictError("Project name already exists")

        project = Project(
            name=data.name,
            description=data.description,
            status=data.status,
            owner_id=owner_id,
            created_by=actor.id,
        )
        self.session.add(project)
        try:
            await self.session.flush()
            record_audit(
                self.session,
                actor_id=actor.id,
                action=AuditAction.CREATE,
                entity=AuditEntity.PROJECT,
                entity_id=project.id,
                project_id=project.id,
            )
            await self.session.commit()
        except IntegrityError as error:
            await self.session.rollback()
            raise ResourceConflictError(
                "Project name already exists"
            ) from error
        await dashboard_cache.invalidate()
        return project

    async def list(
        self,
        filters: ProjectFilters,
        actor: User,
        *,
        limit: int,
        offset: int,
        sort_by: ProjectSortField,
        direction: SortDirection,
    ) -> Page[Project]:
        if (
            filters.owner_id is not None
            and filters.owner_id != actor.id
        ) or (
            filters.created_by is not None
            and filters.created_by != actor.id
        ):
            raise PermissionDeniedError
        return await self.projects.list_filtered(
            ProjectFilters(
                status=filters.status,
                owner_id=actor.id,
                created_by=filters.created_by,
                search=filters.search,
            ),
            limit=limit,
            offset=offset,
            sort_by=sort_by,
            direction=direction,
        )

    async def get(self, project_id: UUID, actor: User) -> Project:
        project = await self.projects.get_owned(project_id, actor.id)
        if project is None:
            raise ResourceNotFoundError("Project not found")
        return project

    async def update(
        self,
        project_id: UUID,
        data: ProjectUpdate,
        actor: User,
    ) -> Project:
        project = await self._get_mutable(project_id, actor)
        if not can_manage_project(actor, project):
            raise PermissionDeniedError

        values = data.model_dump(exclude_unset=True)
        if not values:
            await self.session.rollback()
            return project
        owner_id = values.get("owner_id", project.owner_id)
        if not isinstance(owner_id, UUID):
            raise ResourceConflictError("Project owner is required")
        if owner_id != actor.id:
            raise PermissionDeniedError

        name = values.get("name", project.name)
        if not isinstance(name, str):
            raise ResourceConflictError("Project name is required")
        if (
            name.lower() != project.name.lower()
            or owner_id != project.owner_id
        ) and await self.projects.active_name_exists(
            owner_id=owner_id,
            name=name,
            exclude_id=project.id,
        ):
            raise ResourceConflictError("Project name already exists")

        old_status = project.status
        old_owner_id = project.owner_id
        for field_name, value in values.items():
            setattr(project, field_name, value)

        record_audit(
            self.session,
            actor_id=actor.id,
            action=AuditAction.UPDATE,
            entity=AuditEntity.PROJECT,
            entity_id=project.id,
            project_id=project.id,
        )
        if project.status != old_status:
            record_audit(
                self.session,
                actor_id=actor.id,
                action=AuditAction.STATUS_CHANGE,
                entity=AuditEntity.PROJECT,
                entity_id=project.id,
                project_id=project.id,
            )
            if actor.id != project.owner_id:
                enqueue_project_status_change(
                    self.session,
                    project=project,
                    status=project.status,
                )
        if project.owner_id != old_owner_id:
            record_audit(
                self.session,
                actor_id=actor.id,
                action=AuditAction.ASSIGNMENT,
                entity=AuditEntity.PROJECT,
                entity_id=project.id,
                project_id=project.id,
            )
        await self._commit_duplicate_safe()
        await dashboard_cache.invalidate()
        return project

    async def archive(self, project_id: UUID, actor: User) -> None:
        project = await self._get_mutable(project_id, actor)
        if not can_manage_project(actor, project):
            raise PermissionDeniedError
        project.archived_at = datetime.now(timezone.utc)
        record_audit(
            self.session,
            actor_id=actor.id,
            action=AuditAction.DELETE,
            entity=AuditEntity.PROJECT,
            entity_id=project.id,
            project_id=project.id,
        )
        await self.session.commit()
        await dashboard_cache.invalidate()

    async def _get_mutable(
        self,
        project_id: UUID,
        actor: User,
    ) -> Project:
        project = await self.projects.get_owned_for_update(
            project_id,
            actor.id,
        )
        if project is None:
            raise ResourceNotFoundError("Project not found")
        if project.archived_at is not None:
            raise ResourceConflictError("Archived projects are read-only")
        return project

    async def _commit_duplicate_safe(self) -> None:
        try:
            await self.session.commit()
        except IntegrityError as error:
            await self.session.rollback()
            raise ResourceConflictError(
                "Project name already exists"
            ) from error
