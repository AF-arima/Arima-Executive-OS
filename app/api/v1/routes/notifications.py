from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Query, Response, status

from app.api.v1.dependencies import (
    AUTHENTICATED_RESPONSES,
    AnalyticsUser,
    SessionDependency,
)
from app.database.models import NotificationType
from app.schemas.notification import (
    NotificationList,
    NotificationResponse,
    ReadAllResponse,
    UnreadCountResponse,
)
from app.services.notification import NotificationService

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get(
    "",
    response_model=NotificationList,
    summary="List current user's notifications",
    description=(
        "Returns only the authenticated user's unexpired notifications "
        "using stable newest-first pagination."
    ),
    responses=AUTHENTICATED_RESPONSES,
)
async def list_notifications(
    session: SessionDependency,
    current_user: AnalyticsUser,
    is_read: bool | None = None,
    notification_type: Annotated[
        NotificationType | None,
        Query(alias="type"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> NotificationList:
    return await NotificationService(session).list(
        current_user,
        is_read=is_read,
        notification_type=notification_type,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/unread-count",
    response_model=UnreadCountResponse,
    summary="Get current user's unread notification count",
    responses=AUTHENTICATED_RESPONSES,
)
async def unread_count(
    session: SessionDependency,
    current_user: AnalyticsUser,
) -> UnreadCountResponse:
    return await NotificationService(session).unread_count(current_user)


@router.patch(
    "/{notification_id}/read",
    response_model=NotificationResponse,
    summary="Mark one notification read",
    description="Idempotently marks an owned notification as read.",
    responses={
        **AUTHENTICATED_RESPONSES,
        404: {"description": "Notification not found"},
    },
)
async def mark_notification_read(
    notification_id: UUID,
    session: SessionDependency,
    current_user: AnalyticsUser,
) -> NotificationResponse:
    return await NotificationService(session).mark_read(
        notification_id,
        current_user,
    )


@router.post(
    "/read-all",
    response_model=ReadAllResponse,
    summary="Mark all current notifications read",
    description=(
        "Transactionally and idempotently marks all owned, unexpired "
        "notifications as read."
    ),
    responses=AUTHENTICATED_RESPONSES,
)
async def mark_all_notifications_read(
    session: SessionDependency,
    current_user: AnalyticsUser,
) -> ReadAllResponse:
    return await NotificationService(session).mark_all_read(current_user)


@router.delete(
    "/{notification_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete one owned notification",
    description="Permanently deletes the authenticated user's notification.",
    responses={
        **AUTHENTICATED_RESPONSES,
        404: {"description": "Notification not found"},
    },
)
async def delete_notification(
    notification_id: UUID,
    session: SessionDependency,
    current_user: AnalyticsUser,
) -> Response:
    await NotificationService(session).delete(
        notification_id,
        current_user,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
