import logging
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.dependencies import AUTHENTICATED_RESPONSES, SessionDependency
from app.auth.dependencies import (
    get_current_active_user,
    get_current_user,
    oauth2_scheme,
)
from app.auth.security import SecurityRateLimiter
from app.core.config import get_settings
from app.database.models import User
from app.database.session import get_session
from app.voice.exceptions import (
    VoiceExecutionTimeout,
    VoicePermissionDenied,
    VoiceProviderUnavailable,
    VoiceSessionAccessDenied,
    VoiceSessionBusy,
    VoiceSessionNotFound,
)
from app.voice.factory import VoiceGatewayFactory
from app.voice.schemas import (
    TextToSpeechInput,
    VoiceGatewayResponse,
    VoiceHealth,
    VoiceSession,
    VoiceSessionCreate,
    VoiceTranscriptInput,
)
from app.voice.tts import (
    TTSNotConfigured,
    TTSOrchestrator,
    TTSProviderError,
    TTSRequest,
    TTSTimeout,
    TTSUnsupportedLocale,
)

router = APIRouter(
    prefix="/voice",
    tags=["voice"],
    responses=AUTHENTICATED_RESPONSES,
)
logger = logging.getLogger("arima.request")

_PRE_HANDLER_EVENTS = frozenset(
    {
        "dependency_resolution_start",
        "session_dependency_success",
        "session_dependency_failure",
        "voice_auth_success",
        "voice_auth_failure",
        "db_session_success",
        "db_session_failure",
        "rate_limit_dependency_success",
        "rate_limit_dependency_failure",
        "route_handler_entry",
    }
)
_SAFE_EXCEPTION_TYPES = frozenset(
    {
        "AccountLockedError",
        "CsrfValidationError",
        "EmailNotVerifiedError",
        "InactiveUserError",
        "InvalidTokenError",
        "MFARequiredError",
        "RateLimitExceededError",
        "SQLAlchemyError",
    }
)


def _safe_exception_type(error: BaseException) -> str:
    exception_type = type(error).__name__
    return exception_type if exception_type in _SAFE_EXCEPTION_TYPES else "unknown"


def _emit_pre_handler_event(
    event: str,
    request: Request,
    session_id: UUID,
    *,
    error: BaseException | None = None,
) -> None:
    if event not in _PRE_HANDLER_EVENTS:
        raise ValueError("Unsupported pre-handler diagnostic event")
    payload: dict[str, object] = {
        "event": event,
        "session_id": str(session_id),
        "correlation_id": getattr(request.state, "correlation_id", None),
    }
    if error is not None:
        payload["exception_type"] = _safe_exception_type(error)
    logger.info(event, extra=payload)


async def _trace_dependency_resolution_start(
    request: Request,
    session_id: UUID,
) -> None:
    _emit_pre_handler_event("dependency_resolution_start", request, session_id)


async def _trace_voice_database(
    request: Request,
    session_id: UUID,
) -> AsyncIterator[AsyncSession]:
    session_dependency = request.app.dependency_overrides.get(
        get_session,
        get_session,
    )
    try:
        async for session in session_dependency():
            _emit_pre_handler_event("session_dependency_success", request, session_id)
            _emit_pre_handler_event("db_session_success", request, session_id)
            yield session
    except Exception as error:
        _emit_pre_handler_event(
            "session_dependency_failure",
            request,
            session_id,
            error=error,
        )
        _emit_pre_handler_event(
            "db_session_failure",
            request,
            session_id,
            error=error,
        )
        raise


VoiceDatabase = Annotated[AsyncSession, Depends(_trace_voice_database)]


async def _trace_voice_user(
    request: Request,
    session_id: UUID,
    database: VoiceDatabase,
    token: Annotated[str, Depends(oauth2_scheme)],
) -> User:
    try:
        current_user = await get_current_user(database, token)
        user = await get_current_active_user(current_user)
    except Exception as error:
        _emit_pre_handler_event("voice_auth_failure", request, session_id, error=error)
        raise
    _emit_pre_handler_event("voice_auth_success", request, session_id)
    return user


VoiceUser = Annotated[User, Depends(get_current_active_user)]
TranscriptVoiceUser = Annotated[User, Depends(_trace_voice_user)]


def _boundary_trace_headers(trace: list[str]) -> dict[str, str]:
    return {"X-Arima-Debug-Trace": ",".join(trace)}


def gateway(database: SessionDependency):
    settings = get_settings()
    if not settings.arima_voice_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Voice gateway is disabled",
        )
    return VoiceGatewayFactory(
        database,
        enabled=settings.arima_voice_enabled,
        session_timeout_seconds=settings.arima_voice_session_timeout_seconds,
        execution_timeout_seconds=settings.arima_voice_execution_timeout_seconds,
    ).create()


@router.post(
    "/sessions",
    response_model=VoiceSession,
    status_code=status.HTTP_201_CREATED,
)
async def create_voice_session(
    data: VoiceSessionCreate,
    database: SessionDependency,
    actor: VoiceUser,
) -> VoiceSession:
    voice_gateway = gateway(database)
    try:
        voice_session, _ = await voice_gateway.create_session(data, actor)
    except VoicePermissionDenied as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    return voice_session


@router.get("/sessions/{session_id}", response_model=VoiceSession)
async def get_voice_session(
    session_id: UUID,
    database: SessionDependency,
    actor: VoiceUser,
) -> VoiceSession:
    voice_gateway = gateway(database)
    try:
        return await voice_gateway.sessions.get(session_id, actor.id)
    except VoiceSessionNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except VoiceSessionAccessDenied as error:
        raise HTTPException(status_code=403, detail=str(error)) from error


@router.post(
    "/sessions/{session_id}/transcript",
    response_model=VoiceGatewayResponse,
    dependencies=[Depends(_trace_dependency_resolution_start)],
)
async def submit_voice_transcript(
    session_id: UUID,
    data: VoiceTranscriptInput,
    database: VoiceDatabase,
    actor: TranscriptVoiceUser,
    request: Request,
    response: Response,
) -> VoiceGatewayResponse:
    boundary_trace: list[str] = []
    _emit_pre_handler_event("route_handler_entry", request, session_id)
    logger.info(
        "voice_transcript_route_entry",
        extra={
            "event": "voice_transcript_route_entry",
            "session_id": str(session_id),
            "actor_id": str(actor.id),
        },
    )
    settings = get_settings()
    if len(data.transcript) > settings.arima_voice_max_transcript_length:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Transcript exceeds configured maximum length",
        )
    try:
        await SecurityRateLimiter(database).enforce(
            scope="voice_transcript",
            key=str(actor.id),
            limit=settings.voice_transcript_rate_limit_per_minute,
            window=timedelta(minutes=1),
            session_id=str(session_id),
        )
    except Exception as error:
        _emit_pre_handler_event(
            "rate_limit_dependency_failure", request, session_id, error=error
        )
        raise
    _emit_pre_handler_event("rate_limit_dependency_success", request, session_id)
    voice_gateway = gateway(database)
    try:
        result = await voice_gateway.handle_transcript(
            session_id,
            data.transcript,
            actor,
            correlation_id=getattr(request.state, "correlation_id", None),
            boundary_trace=boundary_trace,
        )
        response.headers.update(_boundary_trace_headers(boundary_trace))
        return result
    except VoiceSessionNotFound as error:
        raise HTTPException(
            status_code=404,
            detail=str(error),
            headers=_boundary_trace_headers(boundary_trace),
        ) from error
    except VoiceSessionAccessDenied as error:
        raise HTTPException(
            status_code=403,
            detail=str(error),
            headers=_boundary_trace_headers(boundary_trace),
        ) from error
    except VoicePermissionDenied as error:
        raise HTTPException(
            status_code=403,
            detail=str(error),
            headers=_boundary_trace_headers(boundary_trace),
        ) from error
    except VoiceSessionBusy as error:
        raise HTTPException(
            status_code=409,
            detail=str(error),
            headers=_boundary_trace_headers(boundary_trace),
        ) from error
    except VoiceExecutionTimeout as error:
        raise HTTPException(
            status_code=504,
            detail=str(error),
            headers=_boundary_trace_headers(boundary_trace),
        ) from error
    except VoiceProviderUnavailable as error:
        raise HTTPException(
            status_code=503,
            detail=str(error),
            headers=_boundary_trace_headers(boundary_trace),
        ) from error


@router.post("/sessions/{session_id}/tts", response_class=Response)
async def synthesize_voice_response(
    session_id: UUID,
    data: TextToSpeechInput,
    database: SessionDependency,
    actor: VoiceUser,
) -> Response:
    settings = get_settings()
    try:
        await SecurityRateLimiter(database).enforce(
            scope="voice_tts",
            key=str(actor.id),
            limit=settings.voice_tts_rate_limit_per_minute,
            window=timedelta(minutes=1),
        )
        session = await VoiceGatewayFactory(database).sessions.get(session_id, actor.id)
        locale = data.locale or session.locale
        result = await TTSOrchestrator(settings).synthesize(
            TTSRequest(
                text=data.text,
                locale=locale,
                request_id=data.request_id,
                generation_id=data.generation_id,
            )
        )
    except VoiceSessionNotFound as error:
        raise HTTPException(status_code=404, detail="Voice session not found") from error
    except VoiceSessionAccessDenied as error:
        raise HTTPException(status_code=403, detail="Voice session access denied") from error
    except TTSUnsupportedLocale as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except TTSNotConfigured as error:
        raise HTTPException(status_code=503, detail="Text-to-speech is not configured") from error
    except TTSTimeout as error:
        raise HTTPException(status_code=504, detail="Text-to-speech timed out") from error
    except TTSProviderError as error:
        raise HTTPException(status_code=503, detail="Text-to-speech is unavailable") from error
    return Response(
        content=result.audio,
        media_type=result.mime_type,
        headers={
            "Cache-Control": "no-store",
            "X-TTS-Provider": result.provider,
            "X-TTS-Locale": result.locale,
            "X-TTS-Request-ID": str(result.request_id),
        },
    )


@router.post(
    "/sessions/{session_id}/interrupt",
    response_model=VoiceGatewayResponse,
)
async def interrupt_voice_session(
    session_id: UUID,
    database: SessionDependency,
    actor: VoiceUser,
) -> VoiceGatewayResponse:
    return await gateway(database).interrupt(session_id, actor)


@router.post(
    "/sessions/{session_id}/cancel",
    response_model=VoiceGatewayResponse,
)
async def cancel_voice_session(
    session_id: UUID,
    database: SessionDependency,
    actor: VoiceUser,
) -> VoiceGatewayResponse:
    return await gateway(database).cancel(session_id, actor)


@router.get("/health", response_model=VoiceHealth)
async def get_voice_health(
    database: SessionDependency,
    actor: VoiceUser,
) -> VoiceHealth:
    del actor
    return await gateway(database).health()
