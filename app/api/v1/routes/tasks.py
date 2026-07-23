from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_any_role
from app.database.models import Task, TaskPriority, TaskStatus, User
from app.database.repositories import TaskFilters
from app.database.session import get_session
from app.schemas.common import SortDirection
from app.schemas.task import (
    TaskCreate,
    TaskListResponse,
    TaskResponse,
    TaskSortField,
    TaskUpdate,
)
from app.services.task import TaskService

router = APIRouter(prefix="/tasks", tags=["tasks"])
SessionDependency = Annotated[AsyncSession, Depends(get_session)]
CurrentUser = Annotated[
    User,
    Depends(
        require_any_role(
            "administrator",
            "executive",
            "manager",
            "analyst",
            "viewer",
        )
    ),
]


@router.post(
    "",
    response_model=TaskResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_task(
    data: TaskCreate,
    session: SessionDependency,
    current_user: CurrentUser,
) -> Task:
    return await TaskService(session).create(data, current_user)


@router.get("", response_model=TaskListResponse)
async def list_tasks(
    session: SessionDependency,
    current_user: CurrentUser,
    task_status: Annotated[
        TaskStatus | None,
        Query(alias="status"),
    ] = None,
    priority: TaskPriority | None = None,
    project: UUID | None = None,
    assigned_to: UUID | None = None,
    overdue: bool | None = None,
    completed: bool | None = None,
    search: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort_by: TaskSortField = TaskSortField.CREATED_AT,
    direction: SortDirection = SortDirection.DESC,
) -> TaskListResponse:
    del current_user
    page = await TaskService(session).list(
        TaskFilters(
            status=task_status,
            priority=priority,
            project_id=project,
            assigned_to=assigned_to,
            overdue=overdue,
            completed=completed,
            search=search,
        ),
        limit=limit,
        offset=offset,
        sort_by=sort_by,
        direction=direction,
    )
    return TaskListResponse(
        items=[TaskResponse.model_validate(task) for task in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: UUID,
    session: SessionDependency,
    current_user: CurrentUser,
) -> Task:
    del current_user
    return await TaskService(session).get(task_id)


@router.patch("/{task_id}", response_model=TaskResponse)
async def update_task(
    task_id: UUID,
    data: TaskUpdate,
    session: SessionDependency,
    current_user: CurrentUser,
) -> Task:
    return await TaskService(session).update(
        task_id,
        data,
        current_user,
    )


@router.delete(
    "/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_task(
    task_id: UUID,
    session: SessionDependency,
    current_user: CurrentUser,
) -> Response:
    await TaskService(session).delete(task_id, current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
