from datetime import timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.api.v1.dependencies import AUTHENTICATED_RESPONSES, SessionDependency
from app.auth.dependencies import get_current_active_user
from app.auth.security import SecurityRateLimiter
from app.core.config import get_settings
from app.database.models import User
from app.voice.exceptions import (
    VoicePermissionDenied,
    VoiceExecutionTimeout,
    VoiceProviderUnavailable,
    VoiceSessionBusy,
    VoiceSessionAccessDenied,
    VoiceSessionNotFound,
)
from app.voice.factory import VoiceGatewayFactory
from app.voice.schemas import (
    VoiceGatewayResponse,
    VoiceHealth,
    VoiceSession,
    VoiceSessionCreate,
    VoiceTranscriptInput,
    TextToSpeechInput,
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
VoiceUser = Annotated[User, Depends(get_current_active_user)]


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
)
async def submit_voice_transcript(
    session_id: UUID,
    data: VoiceTranscriptInput,
    database: SessionDependency,
    actor: VoiceUser,
    request: Request,
    response: Response,
) -> VoiceGatewayResponse:
    boundary_trace: list[str] = []
    settings = get_settings()
    if len(data.transcript) > settings.arima_voice_max_transcript_length:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Transcript exceeds configured maximum length",
        )
    await SecurityRateLimiter(database).enforce(
        scope="voice_transcript",
        key=str(actor.id),
        limit=settings.voice_transcript_rate_limit_per_minute,
        window=timedelta(minutes=1),
    )
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
