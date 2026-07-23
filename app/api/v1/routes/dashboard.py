from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.v1.dependencies import AnalyticsUser, SessionDependency
from app.schemas.analytics import DashboardSummary
from app.services.analytics import AnalyticsService

router = APIRouter(prefix="/dashboard", tags=["dashboard"])
@router.get(
    "/summary",
    response_model=DashboardSummary,
    summary="Get executive dashboard summary",
    description=(
        "Returns permission-scoped metrics for a timezone-aware range. "
        "The default range is the previous 30 days. Rates use a 0–1 "
        "scale. Set refresh=true to bypass and replace the 60-second cache."
    ),
    responses={
        401: {"description": "Missing or invalid access token"},
        403: {"description": "Insufficient permissions"},
        422: {"description": "Invalid range or timezone"},
    },
)
async def dashboard_summary(
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
    owner_id: UUID | None = None,
    assigned_to: UUID | None = None,
    timezone_name: Annotated[
        str,
        Query(
            alias="timezone",
            min_length=1,
            max_length=64,
            description="IANA timezone for default range boundaries",
        ),
    ] = "UTC",
    include_archived: bool = False,
    refresh: Annotated[
        bool,
        Query(description="Bypass and refresh the scoped cache entry"),
    ] = False,
) -> DashboardSummary:
    return await AnalyticsService(session).dashboard_summary(
        current_user,
        start_date=start_date,
        end_date=end_date,
        project_id=project_id,
        owner_id=owner_id,
        assigned_to=assigned_to,
        timezone_name=timezone_name,
        include_archived=include_archived,
        refresh=refresh,
    )
