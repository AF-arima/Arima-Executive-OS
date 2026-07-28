from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_any_role
from app.database.models import Project, ProjectStatus, User
from app.database.repositories import ProjectFilters
from app.database.session import get_session
from app.schemas.common import SortDirection
from app.schemas.project import (
    ProjectCreate,
    ProjectListResponse,
    ProjectResponse,
    ProjectSortField,
    ProjectUpdate,
)
from app.services.project import ProjectService

router = APIRouter(prefix="/projects", tags=["projects"])
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
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_project(
    data: ProjectCreate,
    session: SessionDependency,
    current_user: CurrentUser,
) -> Project:
    return await ProjectService(session).create(data, current_user)


@router.get("", response_model=ProjectListResponse)
async def list_projects(
    session: SessionDependency,
    current_user: CurrentUser,
    project_status: Annotated[
        ProjectStatus | None,
        Query(alias="status"),
    ] = None,
    owner: UUID | None = None,
    created_by: UUID | None = None,
    search: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    sort_by: ProjectSortField = ProjectSortField.CREATED_AT,
    direction: SortDirection = SortDirection.DESC,
) -> ProjectListResponse:
    page = await ProjectService(session).list(
        ProjectFilters(
            status=project_status,
            owner_id=owner,
            created_by=created_by,
            search=search,
        ),
        current_user,
        limit=limit,
        offset=offset,
        sort_by=sort_by,
        direction=direction,
    )
    return ProjectListResponse(
        items=[
            ProjectResponse.model_validate(project)
            for project in page.items
        ],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: UUID,
    session: SessionDependency,
    current_user: CurrentUser,
) -> Project:
    return await ProjectService(session).get(project_id, current_user)


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: UUID,
    data: ProjectUpdate,
    session: SessionDependency,
    current_user: CurrentUser,
) -> Project:
    return await ProjectService(session).update(
        project_id,
        data,
        current_user,
    )


@router.delete(
    "/{project_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_project(
    project_id: UUID,
    session: SessionDependency,
    current_user: CurrentUser,
) -> Response:
    await ProjectService(session).archive(project_id, current_user)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
