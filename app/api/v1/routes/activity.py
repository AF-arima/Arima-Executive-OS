from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query

from app.api.v1.dependencies import (
    AUTHENTICATED_RESPONSES,
    AnalyticsUser,
    SessionDependency,
)
from app.database.models import AuditAction, AuditEntity
from app.schemas.activity import ActivityList
from app.services.activity import ActivityService

router = APIRouter(prefix="/activity", tags=["activity"])


@router.get(
    "",
    response_model=ActivityList,
    summary="Get recent activity",
    description=(
        "Returns safe structured audit activity using the caller's "
        "analytics visibility scope. Deleted task activity is retained "
        "for global scopes; scoped callers only receive derivable records."
    ),
    responses=AUTHENTICATED_RESPONSES,
)
async def list_activity(
    session: SessionDependency,
    current_user: AnalyticsUser,
    actor_id: UUID | None = None,
    entity: AuditEntity | None = None,
    action: AuditAction | None = None,
    project_id: UUID | None = None,
    start_date: Annotated[
        datetime | None,
        Query(description="Timezone-aware inclusive range start"),
    ] = None,
    end_date: Annotated[
        datetime | None,
        Query(description="Timezone-aware inclusive range end"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ActivityList:
    return await ActivityService(session).list(
        current_user,
        actor_id=actor_id,
        entity=entity,
        action=action,
        project_id=project_id,
        start_date=start_date,
        end_date=end_date,
        limit=limit,
        offset=offset,
    )
