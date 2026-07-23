from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    AuditAction,
    AuditEntity,
    Project,
    Task,
    TaskStatus,
    User,
)
from app.database.repositories import (
    Page,
    ProjectRepository,
    TaskFilters,
    TaskRepository,
    UserRepository,
)
from app.schemas.common import SortDirection
from app.schemas.task import TaskCreate, TaskSortField, TaskUpdate
from app.services.audit import record_audit
from app.services.exceptions import (
    PermissionDeniedError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from app.services.permissions import (
    can_create_task,
    can_delete_task,
    can_edit_task,
    user_roles,
)


class TaskService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.tasks = TaskRepository(session)
        self.projects = ProjectRepository(session)
        self.users = UserRepository(session)

    async def create(self, data: TaskCreate, actor: User) -> Task:
        project = await self._get_active_project(data.project_id)
        if not can_create_task(actor, project):
            raise PermissionDeniedError
        if data.assigned_to is not None:
            await self._require_active_user(data.assigned_to)

        task = Task(
            title=data.title,
            description=data.description,
            status=data.status,
            priority=data.priority,
            due_date=data.due_date,
            completed_at=(
                datetime.now(timezone.utc)
                if data.status is TaskStatus.COMPLETED
                else None
            ),
            project_id=data.project_id,
            assignee_id=data.assigned_to,
            created_by=actor.id,
        )
        self.session.add(task)
        await self.session.flush()
        record_audit(
            self.session,
            actor_id=actor.id,
            action=AuditAction.CREATE,
            entity=AuditEntity.TASK,
            entity_id=task.id,
        )
        if data.assigned_to is not None:
            record_audit(
                self.session,
                actor_id=actor.id,
                action=AuditAction.ASSIGNMENT,
                entity=AuditEntity.TASK,
                entity_id=task.id,
            )
        await self.session.commit()
        return task

    async def list(
        self,
        filters: TaskFilters,
        *,
        limit: int,
        offset: int,
        sort_by: TaskSortField,
        direction: SortDirection,
    ) -> Page[Task]:
        return await self.tasks.list_filtered(
            filters,
            now=datetime.now(timezone.utc),
            limit=limit,
            offset=offset,
            sort_by=sort_by,
            direction=direction,
        )

    async def get(self, task_id: UUID) -> Task:
        task = await self.tasks.get(task_id)
        if task is None:
            raise ResourceNotFoundError("Task not found")
        return task

    async def update(
        self,
        task_id: UUID,
        data: TaskUpdate,
        actor: User,
    ) -> Task:
        task = await self.tasks.get_for_update(task_id)
        if task is None:
            raise ResourceNotFoundError("Task not found")
        source_snapshot = await self.projects.get(task.project_id)
        if source_snapshot is None:
            raise ResourceNotFoundError("Project not found")
        if source_snapshot.archived_at is not None:
            raise ResourceConflictError("Archived projects are read-only")
        if not can_edit_task(actor, task, source_snapshot):
            raise PermissionDeniedError

        values = data.model_dump(exclude_unset=True)
        if not values:
            await self.session.rollback()
            return task
        roles = user_roles(actor)
        if (
            "analyst" in roles
            and roles.isdisjoint({"administrator", "executive", "manager"})
            and ("assigned_to" in values or "project_id" in values)
        ):
            raise PermissionDeniedError

        project_id = values.get("project_id")
        destination_id = (
            project_id if isinstance(project_id, UUID) else task.project_id
        )
        locked_projects = await self.projects.get_many_for_update(
            {task.project_id, destination_id}
        )
        projects_by_id = {
            project.id: project for project in locked_projects
        }
        source_project = projects_by_id.get(task.project_id)
        destination = projects_by_id.get(destination_id)
        if source_project is None or destination is None:
            raise ResourceNotFoundError("Project not found")
        if (
            source_project.archived_at is not None
            or destination.archived_at is not None
        ):
            raise ResourceConflictError("Archived projects are read-only")
        if not can_edit_task(actor, task, source_project):
            raise PermissionDeniedError
        if not can_edit_task(actor, task, destination):
            raise PermissionDeniedError

        assigned_to = values.pop("assigned_to", task.assignee_id)
        if assigned_to is not None and not isinstance(assigned_to, UUID):
            raise ResourceConflictError("Invalid assignee")
        if assigned_to is not None and assigned_to != task.assignee_id:
            await self._require_active_user(assigned_to)

        old_status = task.status
        old_assignee = task.assignee_id
        for field_name, value in values.items():
            setattr(task, field_name, value)
        task.assignee_id = assigned_to
        if task.status is TaskStatus.COMPLETED and old_status is not TaskStatus.COMPLETED:
            task.completed_at = datetime.now(timezone.utc)
        elif (
            task.status is not TaskStatus.COMPLETED
            and old_status is TaskStatus.COMPLETED
        ):
            task.completed_at = None

        record_audit(
            self.session,
            actor_id=actor.id,
            action=AuditAction.UPDATE,
            entity=AuditEntity.TASK,
            entity_id=task.id,
        )
        if task.status != old_status:
            record_audit(
                self.session,
                actor_id=actor.id,
                action=AuditAction.STATUS_CHANGE,
                entity=AuditEntity.TASK,
                entity_id=task.id,
            )
        if task.assignee_id != old_assignee:
            record_audit(
                self.session,
                actor_id=actor.id,
                action=AuditAction.ASSIGNMENT,
                entity=AuditEntity.TASK,
                entity_id=task.id,
            )
        await self.session.commit()
        return task

    async def delete(self, task_id: UUID, actor: User) -> None:
        task = await self.tasks.get_for_update(task_id)
        if task is None:
            raise ResourceNotFoundError("Task not found")
        project = await self._get_active_project(task.project_id)
        if not can_delete_task(actor, project):
            raise PermissionDeniedError
        entity_id = task.id
        await self.session.delete(task)
        record_audit(
            self.session,
            actor_id=actor.id,
            action=AuditAction.DELETE,
            entity=AuditEntity.TASK,
            entity_id=entity_id,
        )
        await self.session.commit()

    async def _get_active_project(self, project_id: UUID) -> Project:
        project = await self.projects.get_for_update(project_id)
        if project is None:
            raise ResourceNotFoundError("Project not found")
        if project.archived_at is not None:
            raise ResourceConflictError("Archived projects are read-only")
        return project

    async def _require_active_user(self, user_id: UUID) -> User:
        user = await self.users.get_with_roles(user_id)
        if user is None:
            raise ResourceNotFoundError("User not found")
        if not user.is_active:
            raise ResourceConflictError("Inactive users cannot be assigned")
        return user
