from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.schemas.agent import require_aware
from app.schemas.auth import StrictSchema


class ConfigurationEnvironment(StrictSchema):
    name: str
    present: bool


class ConfigurationStatus(StrictSchema):
    state: Literal[
        "configured",
        "configuration_required",
        "not_applicable",
        "not_configured",
        "manual_only",
    ]
    environment: list[ConfigurationEnvironment]


class FounderHealthComponent(StrictSchema):
    key: str
    label: str
    status: Literal["operational", "configuration_required", "unavailable"]
    checked_at: datetime
    latency_ms: float | None
    provider: str | None
    configuration: ConfigurationStatus
    message: str | None


class FounderSystemHealth(StrictSchema):
    generated_at: datetime
    components: list[FounderHealthComponent]


class FounderFeedError(StrictSchema):
    code: str
    message: str


class FounderDataFeed(StrictSchema):
    key: str
    label: str
    provider: str | None
    status: Literal["live", "manual", "stale", "unavailable"]
    freshness: Literal["current", "stale", "unavailable"]
    last_updated_at: datetime | None
    source: str | None
    entered_by: str | None
    notes: str | None
    expires_at: datetime | None
    errors: list[FounderFeedError]
    configuration: ConfigurationStatus
    manual_entry_supported: bool


class FounderDataFeeds(StrictSchema):
    generated_at: datetime
    feeds: list[FounderDataFeed]


class FounderWorkspaceAgentGrantRead(StrictSchema):
    workspace_id: UUID
    agent_id: UUID
    status: Literal["active"] = "active"


class FounderVoiceGrantTargetRead(StrictSchema):
    workspace_id: UUID
    agent_id: UUID
    agent_name: str
    agent_status: Literal["active"] = "active"


class ManualObservationCreate(StrictSchema):
    source: str = Field(min_length=1, max_length=500)
    observed_at: datetime
    notes: str | None = Field(default=None, max_length=2_000)
    expires_at: datetime | None = None

    _observed_at = field_validator("observed_at")(require_aware)
    _expires_at = field_validator("expires_at")(require_aware)

    @field_validator("source")
    @classmethod
    def normalize_source(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("source cannot be blank")
        return normalized

    @field_validator("notes")
    @classmethod
    def normalize_notes(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_expiry(self) -> "ManualObservationCreate":
        if self.expires_at is not None and self.expires_at <= self.observed_at:
            raise ValueError("expires_at must be later than observed_at")
        return self


class ManualObservationRead(StrictSchema):
    id: UUID
    feed_key: str
    status: Literal["manual"] = "manual"
    source: str
    observed_at: datetime
    notes: str | None
    expires_at: datetime | None
    entered_by: str
    correlation_id: UUID
    created_at: datetime
