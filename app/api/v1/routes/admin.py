from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.csrf import require_valid_csrf
from app.auth.dependencies import (
    require_founder_control,
    require_platform_operator,
)
from app.auth.service import AuthenticationService
from app.database.models import User
from app.database.session import get_session
from app.schemas.auth import CurrentUserResponse, RoleAssignmentRequest
from app.schemas.founder import (
    FounderDataFeeds,
    FounderSystemHealth,
    ManualObservationCreate,
    ManualObservationRead,
)
from app.services.founder_control import FounderControlService

router = APIRouter(prefix="/admin", tags=["administration"])
SessionDependency = Annotated[AsyncSession, Depends(get_session)]
PlatformOperator = Annotated[User, Depends(require_platform_operator)]
FounderControlUser = Annotated[User, Depends(require_founder_control)]


@router.post(
    "/users/{user_id}/roles",
    response_model=CurrentUserResponse,
)
async def assign_role(
    user_id: UUID,
    data: RoleAssignmentRequest,
    session: SessionDependency,
    operator: PlatformOperator,
) -> User:
    return await AuthenticationService(session).assign_role(
        user_id,
        data.role_name,
        actor=operator,
    )


@router.delete(
    "/users/{user_id}/roles/{role_name}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_role(
    user_id: UUID,
    role_name: str,
    session: SessionDependency,
    operator: PlatformOperator,
) -> Response:
    await AuthenticationService(session).remove_role(
        user_id,
        role_name.strip().lower(),
        actor=operator,
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/founder/system-health",
    response_model=FounderSystemHealth,
)
async def founder_system_health(
    session: SessionDependency,
    current_user: FounderControlUser,
) -> FounderSystemHealth:
    del current_user
    return await FounderControlService(session).system_health()


@router.get(
    "/founder/data-feeds",
    response_model=FounderDataFeeds,
)
async def founder_data_feeds(
    session: SessionDependency,
    current_user: FounderControlUser,
) -> FounderDataFeeds:
    del current_user
    return await FounderControlService(session).data_feeds()


@router.post(
    "/founder/data-feeds/{feed_key}/observations",
    response_model=ManualObservationRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_founder_data_feed_observation(
    feed_key: str,
    data: ManualObservationCreate,
    request: Request,
    session: SessionDependency,
    current_user: FounderControlUser,
) -> ManualObservationRead:
    require_valid_csrf(request)
    return await FounderControlService(session).create_manual_observation(
        feed_key=feed_key,
        data=data,
        actor=current_user,
        correlation_id=_request_correlation_id(request),
    )


def _request_correlation_id(request: Request) -> UUID:
    """Read the middleware-provided correlation ID without trusting raw text."""

    try:
        return UUID(str(request.state.correlation_id))
    except (AttributeError, TypeError, ValueError):
        return uuid4()
