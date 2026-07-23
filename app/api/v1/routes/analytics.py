from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.v1.dependencies import (
    AUTHENTICATED_RESPONSES,
    AnalyticsUser,
    SessionDependency,
)
from app.database.models import ProjectStatus, TaskPriority, TaskStatus
from app.schemas.analytics import (
    AnalyticsInterval,
    ProjectAnalyticsList,
    ProjectAnalyticsSortField,
    TaskAnalyticsResponse,
    WorkloadAnalyticsList,
    WorkloadSortField,
)
from app.schemas.common import SortDirection
from app.services.analytics import AnalyticsService

router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get(
    "/projects",
    response_model=ProjectAnalyticsList,
    summary="Get project analytics",
    description=(
        "Returns permission-scoped project aggregates with stable "
        "pagination. Archived projects are excluded by default."
    ),
    responses=AUTHENTICATED_RESPONSES,
)
async def project_analytics(
    session: SessionDependency,
    current_user: AnalyticsUser,
    start_date: Annotated[
        datetime | None,
        Query(description="Timezone-aware inclusive range start"),
    ] = None,
    end_date: Annotated[
        datetime | None,
        Query(description="Timezone-aware inclusive range end"),
    ] = None,
    status: ProjectStatus | None = None,
    owner_id: UUID | None = None,
    include_archived: bool = False,
    search: Annotated[str | None, Query(max_length=200)] = None,
    sort_by: ProjectAnalyticsSortField = (
        ProjectAnalyticsSortField.CREATED_AT
    ),
    direction: SortDirection = SortDirection.DESC,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ProjectAnalyticsList:
    return await AnalyticsService(session).project_analytics(
        current_user,
        start_date=start_date,
        end_date=end_date,
        status=status,
        owner_id=owner_id,
        include_archived=include_archived,
        search=search,
        sort_by=sort_by,
        direction=direction,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/tasks",
    response_model=TaskAnalyticsResponse,
    summary="Get task analytics and time series",
    description=(
        "Returns UTC zero-filled series. Day ranges are limited to "
        "180 days, week ranges to two years, and month ranges to five years."
    ),
    responses=AUTHENTICATED_RESPONSES,
)
async def task_analytics(
    session: SessionDependency,
    current_user: AnalyticsUser,
    start_date: Annotated[
        datetime | None,
        Query(description="Timezone-aware inclusive range start"),
    ] = None,
    end_date: Annotated[
        datetime | None,
        Query(description="Timezone-aware inclusive range end"),
    ] = None,
    project_id: UUID | None = None,
    assigned_to: UUID | None = None,
    status: TaskStatus | None = None,
    priority: TaskPriority | None = None,
    interval: AnalyticsInterval = AnalyticsInterval.DAY,
) -> TaskAnalyticsResponse:
    return await AnalyticsService(session).task_analytics(
        current_user,
        start_date=start_date,
        end_date=end_date,
        project_id=project_id,
        assigned_to=assigned_to,
        status=status,
        priority=priority,
        interval=interval,
    )


@router.get(
    "/workload",
    response_model=WorkloadAnalyticsList,
    summary="Get permission-scoped user workload",
    description=(
        "Workload score = active tasks + 3×overdue + 2×urgent + "
        "high-priority + tasks due within seven days."
    ),
    responses=AUTHENTICATED_RESPONSES,
)
async def workload_analytics(
    session: SessionDependency,
    current_user: AnalyticsUser,
    project_id: UUID | None = None,
    role: Annotated[str | None, Query(max_length=100)] = None,
    active_only: bool = True,
    sort_by: WorkloadSortField = WorkloadSortField.WORKLOAD_SCORE,
    direction: SortDirection = SortDirection.DESC,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> WorkloadAnalyticsList:
    return await AnalyticsService(session).workload_analytics(
        current_user,
        project_id=project_id,
        role=role,
        active_only=active_only,
        sort_by=sort_by,
        direction=direction,
        limit=limit,
        offset=offset,
    )
