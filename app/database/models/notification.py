from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SQLAlchemyEnum,
    ForeignKey,
    Index,
    String,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import Base, UUIDPrimaryKeyMixin, utc_now

if TYPE_CHECKING:
    from app.database.models.user import User


class NotificationType(str, Enum):
    TASK_ASSIGNED = "task_assigned"
    TASK_DUE_SOON = "task_due_soon"
    TASK_OVERDUE = "task_overdue"
    PROJECT_STATUS_CHANGED = "project_status_changed"
    SYSTEM = "system"
    LEAD_ASSIGNED = "lead_assigned"
    DEAL_ASSIGNED = "deal_assigned"
    DEAL_STAGE_CHANGED = "deal_stage_changed"
    LEAD_QUALIFIED = "lead_qualified"
    LEAD_CONVERTED = "lead_converted"
    FOLLOW_UP_DUE = "follow_up_due"
    CRM_ACTIVITY_ASSIGNED = "crm_activity_assigned"
    OUTREACH_APPROVAL_REQUESTED = "outreach_approval_requested"
    OUTREACH_APPROVAL_DECIDED = "outreach_approval_decided"
    OUTREACH_SEND_FAILED = "outreach_send_failed"
    CAMPAIGN_COMPLETED = "campaign_completed"


class Notification(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "notifications"

    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    type: Mapped[NotificationType] = mapped_column(
        SQLAlchemyEnum(
            NotificationType,
            name="notification_type",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=lambda enum: [item.value for item in enum],
        ),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    message: Mapped[str] = mapped_column(String(1000), nullable=False)
    entity_type: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )
    entity_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
    )
    is_read: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    dedupe_key: Mapped[str | None] = mapped_column(
        String(255),
        unique=True,
        nullable=True,
    )

    user: Mapped[User] = relationship(
        back_populates="notifications",
        foreign_keys=[user_id],
    )

    __table_args__ = (
        Index(
            "ix_notifications_user_created",
            user_id,
            created_at,
        ),
        Index(
            "ix_notifications_user_read_created",
            user_id,
            is_read,
            created_at,
        ),
        Index("ix_notifications_expires_at", expires_at),
    )
