from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.experience.schemas import ExperienceEvent
from app.voice.events import VoiceEventType
from app.voice.state import VoiceState
from app.voice.speech import normalize_voice_language


class VoiceSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


VoiceProviderProvenance = Literal["verified", "mock", "unverified"]


class VoiceSessionCreate(VoiceSchema):
    conversation_id: UUID | None = None
    language: str = Field(default="en", min_length=2, max_length=20)
    locale: str = Field(default="en-GB", min_length=2, max_length=35)
    timezone: str = Field(default="Europe/London", min_length=1, max_length=100)

    @field_validator("language")
    @classmethod
    def validate_language(cls, value: str) -> str:
        return normalize_voice_language(value)


class VoiceSession(VoiceSchema):
    session_id: UUID = Field(default_factory=uuid4)
    user_id: UUID
    conversation_id: UUID | None = None
    run_id: UUID | None = None
    correlation_id: UUID = Field(default_factory=uuid4)
    state: VoiceState = VoiceState.IDLE
    language: str
    locale: str
    timezone: str
    transcript: str | None = None
    response_text: str | None = None
    created_at: datetime
    updated_at: datetime


class VoiceTranscriptInput(VoiceSchema):
    transcript: str = Field(min_length=1)
    language: str | None = Field(default=None, min_length=2, max_length=20)
    locale: str | None = Field(default=None, min_length=2, max_length=35)
    timezone: str | None = Field(default=None, min_length=1, max_length=100)

    @field_validator("language")
    @classmethod
    def validate_optional_language(cls, value: str | None) -> str | None:
        return None if value is None else normalize_voice_language(value)


class TextToSpeechInput(VoiceSchema):
    text: str = Field(min_length=1, max_length=10_000)
    locale: str | None = Field(default=None, min_length=2, max_length=35)
    request_id: UUID = Field(default_factory=uuid4)
    generation_id: int | None = Field(default=None, ge=0)


class VoiceCommand(VoiceSchema):
    name: str
    transcript: str
    confidence: float = Field(ge=0, le=1)


class VoiceNavigationAction(VoiceSchema):
    path: str
    label: str
    focus: str | None = None


class VoicePanelAction(VoiceSchema):
    panel: str
    focus: str | None = None


class VoiceApprovalAction(VoiceSchema):
    approval_id: UUID | None = None
    title: str
    reason: str
    policy: str = "user"


class VoiceEvent(VoiceSchema):
    event: VoiceEventType
    sequence: int = Field(ge=0)
    timestamp: datetime
    data: dict[str, Any] = Field(default_factory=dict)


class VoiceGatewayRequest(VoiceSchema):
    session_id: UUID
    transcript: str


class VoiceGatewayResponse(VoiceSchema):
    session_id: UUID
    correlation_id: UUID
    state: VoiceState
    transcript: str | None = None
    response_text: str
    visual_response_text: str
    navigation_action: VoiceNavigationAction | None = None
    panel_action: VoicePanelAction | None = None
    approval_request: VoiceApprovalAction | None = None
    events: list[VoiceEvent] = Field(default_factory=list)
    experience_events: list[ExperienceEvent] = Field(default_factory=list)
    demo: bool = False
    provider_provenance: VoiceProviderProvenance = "unverified"


class VoiceError(VoiceSchema):
    code: str
    message: str
    recoverable: bool = True
    correlation_id: UUID | None = None


class VoiceHealth(VoiceSchema):
    status: str
    enabled: bool
    provider_neutral: bool = True
    session_store: str = "postgresql"
    orchestration_available: bool
    checked_at: datetime
    provider_provenance: VoiceProviderProvenance
    stt_status: str = "not_configured"
    tts_status: str = "not_configured"
    browser_fallback: str = "fallback_only"
