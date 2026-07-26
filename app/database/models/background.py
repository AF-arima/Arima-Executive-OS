from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum as SQLAlchemyEnum,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.background.schemas import (
    BackgroundJobState,
    BackgroundTriggerSource,
    ScheduleType,
)
from app.database.models.base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    utc_now,
)


def background_enum(enum_type: type[Enum], name: str) -> SQLAlchemyEnum:
    return SQLAlchemyEnum(
        enum_type,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda enum: [item.value for item in enum],
    )


class BackgroundJobDefinition(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "background_job_definitions"

    job_name: Mapped[str] = mapped_column(String(150), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str] = mapped_column(String(2000), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    job_type: Mapped[str] = mapped_column(String(50), nullable=False)
    required_permissions: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    approval_policy: Mapped[str] = mapped_column(String(50), nullable=False)
    capabilities: Mapped[list[str]] = mapped_column(
        JSON, default=list, nullable=False
    )
    input_schema: Mapped[dict[str, object]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    output_schema: Mapped[dict[str, object]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "job_name",
            "version",
            name="uq_background_job_definitions_name_version",
        ),
        Index("ix_background_job_definitions_job_name", "job_name"),
    )


class BackgroundJobSchedule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "background_job_schedules"

    job_definition_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("background_job_definitions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    job_name: Mapped[str] = mapped_column(String(150), nullable=False)
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agent_definitions.id", ondelete="CASCADE"),
        nullable=False,
    )
    conversation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agent_conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    schedule_type: Mapped[ScheduleType] = mapped_column(
        background_enum(ScheduleType, "background_schedule_type"),
        nullable=False,
    )
    status: Mapped[BackgroundJobState] = mapped_column(
        background_enum(BackgroundJobState, "background_job_state_schedule"),
        default=BackgroundJobState.SCHEDULED,
        nullable=False,
    )
    definition: Mapped[dict[str, object]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    timezone: Mapped[str] = mapped_column(
        String(100), default="UTC", nullable=False
    )
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    last_run_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    maximum_runs: Mapped[int | None] = mapped_column(Integer)
    run_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    paused: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    __table_args__ = (
        Index("ix_background_job_schedules_status", "status"),
        Index("ix_background_job_schedules_next_run_at", "next_run_at"),
        Index("ix_background_job_schedules_job_name", "job_name"),
        Index("ix_background_job_schedules_agent_id", "agent_id"),
        Index("ix_background_job_schedules_user_id", "user_id"),
    )


class BackgroundJobExecution(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "background_job_executions"

    job_definition_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("background_job_definitions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    schedule_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("background_job_schedules.id", ondelete="SET NULL"),
    )
    job_name: Mapped[str] = mapped_column(String(150), nullable=False)
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agent_definitions.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    correlation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False
    )
    trigger_source: Mapped[BackgroundTriggerSource] = mapped_column(
        background_enum(
            BackgroundTriggerSource, "background_trigger_source"
        ),
        nullable=False,
    )
    status: Mapped[BackgroundJobState] = mapped_column(
        background_enum(
            BackgroundJobState, "background_job_state_execution"
        ),
        default=BackgroundJobState.PENDING,
        nullable=False,
    )
    input_payload: Mapped[dict[str, object]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    result_payload: Mapped[dict[str, object] | None] = mapped_column(JSON)
    dry_run: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    duration_ms: Mapped[float | None] = mapped_column(Float)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(String(2000))

    __table_args__ = (
        Index("ix_background_job_executions_status", "status"),
        Index("ix_background_job_executions_job_name", "job_name"),
        Index("ix_background_job_executions_agent_id", "agent_id"),
        Index("ix_background_job_executions_user_id", "user_id"),
        Index(
            "ix_background_job_executions_correlation_id",
            "correlation_id",
        ),
    )


class BackgroundJobAttempt(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "background_job_attempts"

    execution_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("background_job_executions.id", ondelete="CASCADE"),
        nullable=False,
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[BackgroundJobState] = mapped_column(
        background_enum(BackgroundJobState, "background_job_state_attempt"),
        nullable=False,
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    duration_ms: Mapped[float | None] = mapped_column(Float)
    result_payload: Mapped[dict[str, object] | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(String(2000))

    __table_args__ = (
        UniqueConstraint(
            "execution_id",
            "attempt_number",
            name="uq_background_job_attempts_execution_attempt",
        ),
        Index("ix_background_job_attempts_status", "status"),
    )


class BackgroundJobEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "background_job_events"

    execution_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("background_job_executions.id", ondelete="CASCADE"),
    )
    schedule_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("background_job_schedules.id", ondelete="CASCADE"),
    )
    job_name: Mapped[str] = mapped_column(String(150), nullable=False)
    correlation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False
    )
    from_state: Mapped[BackgroundJobState | None] = mapped_column(
        background_enum(BackgroundJobState, "background_job_state_event_from")
    )
    to_state: Mapped[BackgroundJobState] = mapped_column(
        background_enum(BackgroundJobState, "background_job_state_event_to"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    event_metadata: Mapped[dict[str, object]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    __table_args__ = (
        Index("ix_background_job_events_job_name", "job_name"),
        Index("ix_background_job_events_correlation_id", "correlation_id"),
        Index("ix_background_job_events_to_state", "to_state"),
    )
