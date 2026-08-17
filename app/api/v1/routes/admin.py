from typing import Annotated
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.csrf import require_valid_csrf
from app.auth.dependencies import (
    require_founder_control,
    require_platform_operator,
)
from app.auth.service import AuthenticationService
from app.database.models import AgentStatus, AuditAction, AuditEntity, User
from app.database.repositories.agent import AgentDefinitionRepository
from app.database.repositories.workspace import WorkspaceRepository
from app.database.session import get_session
from app.intelligence.access import AgentGrantService, IntelligenceAccessError
from app.schemas.auth import CurrentUserResponse, RoleAssignmentRequest
from app.schemas.founder import (
    FounderDataFeeds,
    FounderSystemHealth,
    FounderVoiceGrantTargetRead,
    FounderWorkspaceAgentGrantRead,
    ManualObservationCreate,
    ManualObservationRead,
)
from app.schemas.voice_diagnostic import VoiceAuthorizationDiagnostic
from app.services.founder_control import FounderControlService
from app.services.audit import record_audit
from app.services.voice_authorization_diagnostic import (
    VoiceAuthorizationDiagnosticService,
)

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


@router.get(
    "/founder/voice/sessions/{session_id}/authorization-diagnostic",
    response_model=VoiceAuthorizationDiagnostic,
)
async def founder_voice_authorization_diagnostic(
    session_id: UUID,
    session: SessionDependency,
    current_user: FounderControlUser,
) -> VoiceAuthorizationDiagnostic:
    return await VoiceAuthorizationDiagnosticService(session).inspect(
        session_id,
        operator=current_user,
    )


@router.get(
    "/founder/voice/grant-target",
    response_model=FounderVoiceGrantTargetRead,
)
async def founder_voice_grant_target(
    session: SessionDependency,
    current_user: FounderControlUser,
) -> FounderVoiceGrantTargetRead:
    workspace = await WorkspaceRepository(session).get_by_owner(current_user.id)
    agent = await AgentDefinitionRepository(session).get_active_default()
    if workspace is None or agent is None or agent.status is not AgentStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Voice grant target is unavailable",
        )
    return FounderVoiceGrantTargetRead(
        workspace_id=workspace.id,
        agent_id=agent.id,
        agent_name=agent.name,
    )


@router.post(
    "/founder/workspaces/{workspace_id}/agents/{agent_id}/grant",
    response_model=FounderWorkspaceAgentGrantRead,
    status_code=status.HTTP_200_OK,
)
async def founder_grant_workspace_agent(
    workspace_id: UUID,
    agent_id: UUID,
    request: Request,
    session: SessionDependency,
    current_user: FounderControlUser,
) -> FounderWorkspaceAgentGrantRead:
    require_valid_csrf(request)
    try:
        grant = await AgentGrantService(session).grant(
            workspace_id=workspace_id,
            agent_id=agent_id,
            actor=current_user,
        )
    except IntelligenceAccessError as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Permission denied",
        ) from error
    record_audit(
        session,
        actor_id=current_user.id,
        action=AuditAction.ASSIGNMENT,
        entity=AuditEntity.AUTOMATION,
        entity_id=grant.id,
    )
    await session.commit()
    return FounderWorkspaceAgentGrantRead(
        workspace_id=grant.workspace_id,
        agent_id=grant.agent_id,
    )


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
