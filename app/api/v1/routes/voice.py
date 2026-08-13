from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.v1.dependencies import AUTHENTICATED_RESPONSES, SessionDependency
from app.auth.dependencies import get_current_active_user
from app.core.config import get_settings
from app.database.models import User
from app.voice.exceptions import (
    VoicePermissionDenied,
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
)

router = APIRouter(
    prefix="/voice",
    tags=["voice"],
    responses=AUTHENTICATED_RESPONSES,
)
VoiceUser = Annotated[User, Depends(get_current_active_user)]


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
    voice_session, _ = await voice_gateway.create_session(data, actor)
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
) -> VoiceGatewayResponse:
    settings = get_settings()
    if len(data.transcript) > settings.arima_voice_max_transcript_length:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Transcript exceeds configured maximum length",
        )
    voice_gateway = gateway(database)
    try:
        return await voice_gateway.handle_transcript(
            session_id, data.transcript, actor
        )
    except VoiceSessionNotFound as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except VoiceSessionAccessDenied as error:
        raise HTTPException(status_code=403, detail=str(error)) from error
    except VoicePermissionDenied as error:
        raise HTTPException(status_code=403, detail=str(error)) from error


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
