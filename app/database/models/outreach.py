from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class MailboxProvider(str, Enum):
    GMAIL = "gmail"
    MICROSOFT_365 = "microsoft_365"
    SMTP = "smtp"


class OutreachStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class DraftStatus(str, Enum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    SCHEDULED = "scheduled"
    QUEUED = "queued"
    SENT = "sent"
    CANCELLED = "cancelled"


class QueueStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    SENT = "sent"
    RETRY = "retry"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DeliveryEventType(str, Enum):
    SENT = "sent"
    DELIVERED = "delivered"
    OPENED = "opened"
    CLICKED = "clicked"
    REPLIED = "replied"
    BOUNCED = "bounced"
    UNSUBSCRIBED = "unsubscribed"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class AutomationTrigger(str, Enum):
    LEAD_CREATED = "lead_created"
    LEAD_QUALIFIED = "lead_qualified"
    DEAL_STAGE_CHANGED = "deal_stage_changed"
    EMAIL_REPLIED = "email_replied"
    EMAIL_BOUNCED = "email_bounced"
    CAMPAIGN_COMPLETED = "campaign_completed"


class AutomationAction(str, Enum):
    ENROLL_SEQUENCE = "enroll_sequence"
    SEND_NOTIFICATION = "send_notification"


def enum_type(enum: type[Enum], name: str) -> SAEnum:
    return SAEnum(
        enum,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda items: [item.value for item in items],
    )


class MailboxConnection(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "outreach_mailboxes"

    provider: Mapped[MailboxProvider] = mapped_column(
        enum_type(MailboxProvider, "mailbox_provider"), nullable=False
    )
    email_address: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(200))
    credential_reference: Mapped[str] = mapped_column(String(500), nullable=False)
    signature_html: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    daily_send_limit: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    owner_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("owner_id", "email_address"),
        CheckConstraint(
            "daily_send_limit > 0 AND daily_send_limit <= 10000",
            name="daily_limit_range",
        ),
        Index("ix_outreach_mailboxes_owner_active", "owner_id", "is_active"),
    )


class EmailTemplate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "outreach_templates"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000))
    owner_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    versions: Mapped[list[EmailTemplateVersion]] = relationship(
        back_populates="template",
        cascade="all, delete-orphan",
        order_by="EmailTemplateVersion.version",
    )

    __table_args__ = (
        UniqueConstraint("owner_id", "name"),
        Index("ix_outreach_templates_owner_archived", "owner_id", "is_archived"),
    )


class EmailTemplateVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "outreach_template_versions"

    template_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("outreach_templates.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    body_html: Mapped[str] = mapped_column(Text, nullable=False)
    body_text: Mapped[str | None] = mapped_column(Text)
    variables: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    created_by: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    template: Mapped[EmailTemplate] = relationship(back_populates="versions")

    __table_args__ = (
        UniqueConstraint("template_id", "version"),
        CheckConstraint("version > 0", name="version_positive"),
    )


class EmailDraft(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "outreach_drafts"

    mailbox_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("outreach_mailboxes.id", ondelete="RESTRICT"),
        nullable=False,
    )
    template_version_id: Mapped[UUID | None] = mapped_column(
        Uuid(),
        ForeignKey("outreach_template_versions.id", ondelete="SET NULL"),
    )
    contact_id: Mapped[UUID | None] = mapped_column(
        Uuid(), ForeignKey("crm_contacts.id", ondelete="SET NULL")
    )
    to_email: Mapped[str] = mapped_column(String(320), nullable=False)
    cc: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    bcc: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    subject: Mapped[str] = mapped_column(String(500), nullable=False)
    body_html: Mapped[str] = mapped_column(Text, nullable=False)
    body_text: Mapped[str | None] = mapped_column(Text)
    variable_values: Mapped[dict[str, str]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    status: Mapped[DraftStatus] = mapped_column(
        enum_type(DraftStatus, "outreach_draft_status"),
        default=DraftStatus.DRAFT,
        nullable=False,
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    owner_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    attachments: Mapped[list[EmailAttachment]] = relationship(
        back_populates="draft", cascade="all, delete-orphan"
    )

    __table_args__ = (
        Index("ix_outreach_drafts_owner_status", "owner_id", "status"),
        Index("ix_outreach_drafts_scheduled_at", "scheduled_at"),
    )


class EmailAttachment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "outreach_attachments"

    draft_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("outreach_drafts.id", ondelete="CASCADE"), nullable=False
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(200), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1000), nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    draft: Mapped[EmailDraft] = relationship(back_populates="attachments")

    __table_args__ = (
        CheckConstraint(
            "size_bytes >= 0 AND size_bytes <= 26214400",
            name="attachment_size_range",
        ),
    )


class Sequence(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "outreach_sequences"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000))
    status: Mapped[OutreachStatus] = mapped_column(
        enum_type(OutreachStatus, "outreach_sequence_status"),
        default=OutreachStatus.DRAFT,
        nullable=False,
    )
    owner_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    steps: Mapped[list[SequenceStep]] = relationship(
        back_populates="sequence",
        cascade="all, delete-orphan",
        order_by="SequenceStep.position",
    )

    __table_args__ = (
        UniqueConstraint("owner_id", "name"),
        Index("ix_outreach_sequences_owner_status", "owner_id", "status"),
    )


class SequenceStep(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "outreach_sequence_steps"

    sequence_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("outreach_sequences.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    delay_minutes: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    template_version_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("outreach_template_versions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    requires_approval: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    sequence: Mapped[Sequence] = relationship(back_populates="steps")

    __table_args__ = (
        UniqueConstraint("sequence_id", "position"),
        CheckConstraint("position >= 0", name="position_nonnegative"),
        CheckConstraint("delay_minutes >= 0", name="delay_nonnegative"),
    )


class DynamicAudience(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "outreach_audiences"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    filter_definition: Mapped[dict[str, object]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    owner_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    __table_args__ = (UniqueConstraint("owner_id", "name"),)


class Campaign(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "outreach_campaigns"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    sequence_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("outreach_sequences.id", ondelete="RESTRICT"), nullable=False
    )
    audience_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("outreach_audiences.id", ondelete="RESTRICT"), nullable=False
    )
    mailbox_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("outreach_mailboxes.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[OutreachStatus] = mapped_column(
        enum_type(OutreachStatus, "outreach_campaign_status"),
        default=OutreachStatus.DRAFT,
        nullable=False,
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    owner_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("owner_id", "name"),
        Index("ix_outreach_campaigns_owner_status", "owner_id", "status"),
        Index("ix_outreach_campaigns_scheduled_at", "scheduled_at"),
    )


class SequenceEnrollment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "outreach_enrollments"

    sequence_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("outreach_sequences.id", ondelete="RESTRICT"), nullable=False
    )
    campaign_id: Mapped[UUID | None] = mapped_column(
        Uuid(), ForeignKey("outreach_campaigns.id", ondelete="SET NULL")
    )
    contact_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("crm_contacts.id", ondelete="CASCADE"), nullable=False
    )
    current_step: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[OutreachStatus] = mapped_column(
        enum_type(OutreachStatus, "outreach_enrollment_status"),
        default=OutreachStatus.ACTIVE,
        nullable=False,
    )
    next_execution_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    owner_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("sequence_id", "contact_id"),
        Index(
            "ix_outreach_enrollments_status_next",
            "status",
            "next_execution_at",
        ),
    )


class SendQueueItem(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "outreach_send_queue"

    draft_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("outreach_drafts.id", ondelete="CASCADE"), nullable=False
    )
    enrollment_id: Mapped[UUID | None] = mapped_column(
        Uuid(), ForeignKey("outreach_enrollments.id", ondelete="SET NULL")
    )
    status: Mapped[QueueStatus] = mapped_column(
        enum_type(QueueStatus, "outreach_queue_status"),
        default=QueueStatus.PENDING,
        nullable=False,
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    attempt_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5, nullable=False)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_message_id: Mapped[str | None] = mapped_column(String(500))
    last_error_code: Mapped[str | None] = mapped_column(String(100))

    __table_args__ = (
        UniqueConstraint("draft_id"),
        CheckConstraint(
            "attempt_count >= 0 AND max_attempts > 0",
            name="attempts_valid",
        ),
        Index("ix_outreach_queue_status_available", "status", "available_at"),
    )


class SequenceExecution(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "outreach_executions"

    enrollment_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("outreach_enrollments.id", ondelete="CASCADE"),
        nullable=False,
    )
    step_id: Mapped[UUID] = mapped_column(
        Uuid(),
        ForeignKey("outreach_sequence_steps.id", ondelete="RESTRICT"),
        nullable=False,
    )
    queue_item_id: Mapped[UUID | None] = mapped_column(
        Uuid(), ForeignKey("outreach_send_queue.id", ondelete="SET NULL")
    )
    status: Mapped[QueueStatus] = mapped_column(
        enum_type(QueueStatus, "outreach_execution_status"), nullable=False
    )
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (UniqueConstraint("enrollment_id", "step_id"),)


class DeliveryEvent(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "outreach_delivery_events"

    queue_item_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("outreach_send_queue.id", ondelete="CASCADE"), nullable=False
    )
    type: Mapped[DeliveryEventType] = mapped_column(
        enum_type(DeliveryEventType, "outreach_delivery_event_type"),
        nullable=False,
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    provider_event_id: Mapped[str] = mapped_column(String(500), nullable=False)
    safe_metadata: Mapped[dict[str, object]] = mapped_column(
        JSON, default=dict, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("provider_event_id"),
        Index("ix_outreach_events_queue_type", "queue_item_id", "type"),
        Index("ix_outreach_events_occurred_at", "occurred_at"),
    )


class OutreachApproval(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "outreach_approvals"

    draft_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("outreach_drafts.id", ondelete="CASCADE"), nullable=False
    )
    requested_by: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    reviewer_id: Mapped[UUID | None] = mapped_column(
        Uuid(), ForeignKey("users.id", ondelete="SET NULL")
    )
    status: Mapped[ApprovalStatus] = mapped_column(
        enum_type(ApprovalStatus, "outreach_approval_status"),
        default=ApprovalStatus.PENDING,
        nullable=False,
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_reason: Mapped[str | None] = mapped_column(String(1000))

    __table_args__ = (
        UniqueConstraint("draft_id"),
        Index("ix_outreach_approvals_reviewer_status", "reviewer_id", "status"),
    )


class AutomationRule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "outreach_automations"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    trigger: Mapped[AutomationTrigger] = mapped_column(
        enum_type(AutomationTrigger, "outreach_automation_trigger"),
        nullable=False,
    )
    action: Mapped[AutomationAction] = mapped_column(
        enum_type(AutomationAction, "outreach_automation_action"),
        nullable=False,
    )
    conditions: Mapped[dict[str, object]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    action_config: Mapped[dict[str, object]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    owner_id: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[UUID] = mapped_column(
        Uuid(), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("owner_id", "name"),
        Index("ix_outreach_automations_trigger_active", "trigger", "is_active"),
    )
