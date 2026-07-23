from datetime import datetime, timezone
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.database.models import AuditAction, AuditEntity


class ActivityItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    actor_id: UUID | None
    action: AuditAction
    entity: AuditEntity
    entity_id: UUID
    timestamp: datetime
    summary: str
    metadata: dict[str, str | UUID | None]

    @field_validator("timestamp", mode="after")
    @classmethod
    def ensure_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class ActivityList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ActivityItem]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
