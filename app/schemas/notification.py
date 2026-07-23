from datetime import datetime, timezone
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.database.models import NotificationType


class NotificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    type: NotificationType
    title: str
    message: str
    entity_type: str | None
    entity_id: UUID | None
    is_read: bool
    read_at: datetime | None
    created_at: datetime
    expires_at: datetime | None

    @field_validator(
        "read_at",
        "created_at",
        "expires_at",
        mode="after",
    )
    @classmethod
    def ensure_timezone(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class NotificationList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[NotificationResponse]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)


class UnreadCountResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    unread_count: int = Field(ge=0)


class ReadAllResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    updated_count: int = Field(ge=0)
