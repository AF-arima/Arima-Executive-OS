from typing import Annotated
from time import perf_counter
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
from app.providers import (
    CompletionRequest,
    ProviderFactory,
    ProviderMessage,
    ProviderName,
)
from app.providers.types import MessageRole
from app.schemas.auth import CurrentUserResponse, RoleAssignmentRequest
from app.schemas.founder import (
    FounderDataFeeds,
    FounderSystemHealth,
    FounderVoiceGrantTargetRead,
    FounderWorkspaceAgentGrantRead,
    GroqSmokeTestResponse,
    ManualObservationCreate,
    ManualObservationRead,
)
from app.schemas.voice_diagnostic import VoiceAuthorizationDiagnostic
from app.services.founder_control import FounderControlService
from app.services.audit import record_audit
from app.services.voice_authorization_diagnostic import (
    VoiceAuthorizationDiagnosticService,
)
from app.voice.observability import VoiceExecutionObserver, normalized_failure_class

router = APIRouter(prefix="/admin", tags=["administration"])
SessionDependency = Annotated[AsyncSession, Depends(get_session)]
PlatformOperator = Annotated[User, Depends(require_platform_operator)]
FounderControlUser = Annotated[User, Depends(require_founder_control)]

_GROQ_FAILURE_CATEGORIES = frozenset(
    {
        "unauthorized",
        "forbidden",
        "not_found",
        "rate_limited",
        "bad_request",
        "server_error",
        "timeout",
        "transport_error",
        "provider_error",
        "parser_error",
        "unknown",
    }
)


def _groq_failure_details(
    failure: dict[str, object],
    error: BaseException,
) -> dict[str, object | None]:
    status_code = failure.get("status_code")
    safe_status = (
        status_code
        if (
            isinstance(status_code, int)
            and not isinstance(status_code, bool)
            and 100 <= status_code <= 599
        )
        else None
    )
    if safe_status == 401:
        failure_class = "unauthorized"
    elif safe_status == 403:
        failure_class = "forbidden"
    elif safe_status == 404:
        failure_class = "not_found"
    elif safe_status == 429:
        failure_class = "rate_limited"
    elif safe_status == 408:
        failure_class = "timeout"
    elif safe_status is not None and 400 <= safe_status <= 499:
        failure_class = "bad_request"
    elif safe_status is not None and 500 <= safe_status <= 599:
        failure_class = "server_error"
    else:
        existing_class = failure.get("failure_class")
        failure_class = {
            "provider_auth_error": "provider_error",
            "provider_rate_limit": "rate_limited",
            "provider_timeout": "timeout",
            "provider_connection_error": "transport_error",
            "provider_http_error": "provider_error",
            "provider_unavailable": "provider_error",
        }.get(existing_class, existing_class)
        if failure_class not in _GROQ_FAILURE_CATEGORIES:
            failure_class = {
                "ProviderTimeout": "timeout",
                "RateLimitExceeded": "rate_limited",
                "AuthenticationFailure": "provider_error",
            }.get(type(error).__name__, "unknown")
    exception_type = failure.get("exception_type")
    if exception_type not in _GROQ_FAILURE_CATEGORIES:
        exception_type = failure_class
    if exception_type not in _GROQ_FAILURE_CATEGORIES:
        exception_type = "unknown"
    return {
        "http_status": safe_status,
        "exception_type": exception_type,
        "failure_class": failure_class,
    }


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


@router.post(
    "/founder/diagnostics/groq-smoke",
    response_model=GroqSmokeTestResponse,
)
async def founder_groq_smoke_test(
    request: Request,
    current_user: FounderControlUser,
) -> GroqSmokeTestResponse:
    del current_user
    require_valid_csrf(request)
    events: list[dict[str, object]] = []
    observer = VoiceExecutionObserver(
        None,
        uuid4(),
        sink=lambda _event, payload: events.append(payload),
    )
    completion_request = CompletionRequest(
        model="openai/gpt-oss-20b",
        messages=(
            ProviderMessage(
                role=MessageRole.USER,
                content="Reply with exactly: GROQ_SMOKE_OK",
            ),
        ),
        max_output_tokens=32,
        metadata={
            "_voice_observer": observer,
            "voice_session_id": observer.session_id,
            "voice_trace_id": observer.request_id,
        },
    )
    started = perf_counter()
    try:
        provider = ProviderFactory().create(
            provider=ProviderName.GROQ,
            model="openai/gpt-oss-20b",
        )
        result = await provider.complete(completion_request)
    except Exception as error:
        elapsed_ms = round((perf_counter() - started) * 1000, 2)
        failure = next(
            (
                event
                for event in reversed(events)
                if event.get("event") == "provider_attempt_failure"
            ),
            {},
        )
        failure_details = _groq_failure_details(failure, error)
        return GroqSmokeTestResponse(
            success=False,
            http_status_category=(
                failure.get("status_category")
                if isinstance(failure.get("status_category"), str)
                else None
            ),
            elapsed_ms=elapsed_ms,
            parser="fail",
            completion_matches=False,
            telemetry="pass" if failure else "fail",
            error=(
                failure.get("failure_class")
                if isinstance(failure.get("failure_class"), str)
                else normalized_failure_class(error)
            ),
            **failure_details,
        )
    elapsed_ms = round((perf_counter() - started) * 1000, 2)
    response_event = next(
        (
            event
            for event in reversed(events)
            if event.get("event") == "provider_response_received"
        ),
        {},
    )
    return GroqSmokeTestResponse(
        success=True,
        http_status_category=(
            response_event.get("status_category")
            if isinstance(response_event.get("status_category"), str)
            else "2xx"
        ),
        elapsed_ms=elapsed_ms,
        parser="pass",
        completion_matches=result.content == "GROQ_SMOKE_OK",
        telemetry=(
            "pass"
            if any(event.get("event") == "provider_attempt_success" for event in events)
            else "fail"
        ),
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
