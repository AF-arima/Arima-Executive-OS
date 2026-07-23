from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    DateTime,
    Enum as SQLAlchemyEnum,
    ForeignKey,
    Index,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import Base, UUIDPrimaryKeyMixin, utc_now

if TYPE_CHECKING:
    from app.database.models.user import User


class AuditAction(str, Enum):
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    ASSIGNMENT = "assignment"
    STATUS_CHANGE = "status_change"
    CONVERT = "convert"
    STAGE_CHANGE = "stage_change"
    COMPLETE = "complete"


class AuditEntity(str, Enum):
    PROJECT = "project"
    TASK = "task"
    COMPANY = "company"
    CONTACT = "contact"
    LEAD = "lead"
    PIPELINE = "pipeline"
    PIPELINE_STAGE = "pipeline_stage"
    DEAL = "deal"
    CRM_NOTE = "crm_note"
    CRM_ACTIVITY = "crm_activity"


class AuditLog(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "audit_logs"

    actor_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    action: Mapped[AuditAction] = mapped_column(
        SQLAlchemyEnum(
            AuditAction,
            name="audit_action",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=lambda enum: [item.value for item in enum],
        ),
        nullable=False,
    )
    entity: Mapped[AuditEntity] = mapped_column(
        SQLAlchemyEnum(
            AuditEntity,
            name="audit_entity",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            values_callable=lambda enum: [item.value for item in enum],
        ),
        nullable=False,
    )
    entity_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        index=True,
        nullable=False,
    )
    project_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        index=True,
        nullable=False,
    )

    actor: Mapped[User | None] = relationship(
        back_populates="audit_logs",
        foreign_keys=[actor_id],
    )

    __table_args__ = (
        Index(
            "ix_audit_logs_actor_timestamp",
            actor_id,
            timestamp,
        ),
        Index(
            "ix_audit_logs_entity_action_timestamp",
            entity,
            action,
            timestamp,
        ),
        Index(
            "ix_audit_logs_project_timestamp",
            project_id,
            timestamp,
        ),
    )
