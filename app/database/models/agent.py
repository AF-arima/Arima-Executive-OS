from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum as SQLAlchemyEnum,
    ForeignKey,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    utc_now,
)

if TYPE_CHECKING:
    from app.database.models.user import User


class AgentStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    DISABLED = "disabled"
    ARCHIVED = "archived"


class ConversationStatus(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    CLOSED = "closed"


class ConversationPriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class MessageRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
    APPROVAL = "approval"


class MessageContentType(str, Enum):
    TEXT = "text"
    JSON = "json"
    MARKDOWN = "markdown"


class AgentRunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentRiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ToolExecutionMode(str, Enum):
    INTERNAL = "internal"
    PROVIDER = "provider"
    DEFERRED = "deferred"


class ToolExecutionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AgentApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    CANCELLED = "cancelled"


class AgentMemoryType(str, Enum):
    PREFERENCE = "preference"
    FACT = "fact"
    DECISION = "decision"
    POLICY = "policy"
    SUMMARY = "summary"
    INSTRUCTION = "instruction"


class AgentMemoryScope(str, Enum):
    USER = "user"
    AGENT = "agent"
    CONVERSATION = "conversation"
    ORGANISATION = "organisation"


class AgentAttachmentStatus(str, Enum):
    PENDING = "pending"
    AVAILABLE = "available"
    FAILED = "failed"
    DELETED = "deleted"


def agent_enum(enum_type: type[Enum], name: str) -> SQLAlchemyEnum:
    return SQLAlchemyEnum(
        enum_type,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda enum: [item.value for item in enum],
    )


class AgentDefinition(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agent_definitions"

    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(2000))
    system_instructions: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[AgentStatus] = mapped_column(
        agent_enum(AgentStatus, "agent_status"),
        default=AgentStatus.DRAFT,
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    is_default: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    created_by_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_by: Mapped[User] = relationship(foreign_keys=[created_by_id])
    conversations: Mapped[list[AgentConversation]] = relationship(
        back_populates="agent"
    )
    runs: Mapped[list[AgentRun]] = relationship(
        back_populates="agent",
        foreign_keys="AgentRun.agent_id",
    )

    __table_args__ = (
        UniqueConstraint("slug", name="uq_agent_definitions_slug"),
        CheckConstraint("version > 0", name="agent_version_positive"),
        Index(
            "uq_agent_definitions_active_default",
            "is_default",
            unique=True,
            postgresql_where=text(
                "is_default AND status = 'active' AND archived_at IS NULL"
            ),
            sqlite_where=text(
                "is_default = 1 AND status = 'active' "
                "AND archived_at IS NULL"
            ),
        ),
        Index("ix_agent_definitions_status", "status"),
    )


class AgentConversation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agent_conversations"

    agent_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agent_definitions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    owner_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(300), nullable=False)
    status: Mapped[ConversationStatus] = mapped_column(
        agent_enum(ConversationStatus, "agent_conversation_status"),
        default=ConversationStatus.ACTIVE,
        nullable=False,
    )
    priority: Mapped[ConversationPriority] = mapped_column(
        agent_enum(ConversationPriority, "agent_conversation_priority"),
        default=ConversationPriority.NORMAL,
        nullable=False,
    )
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    metadata_: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )
    last_message_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    agent: Mapped[AgentDefinition] = relationship(back_populates="conversations")
    owner: Mapped[User] = relationship(foreign_keys=[owner_id])
    messages: Mapped[list[AgentMessage]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="AgentMessage.sequence_number",
        foreign_keys="AgentMessage.conversation_id",
    )
    runs: Mapped[list[AgentRun]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        foreign_keys="AgentRun.conversation_id",
    )
    attachments: Mapped[list[AgentAttachment]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index(
            "ix_agent_conversations_owner_status_updated",
            "owner_id",
            "status",
            "updated_at",
        ),
        Index(
            "ix_agent_conversations_agent_status",
            "agent_id",
            "status",
        ),
    )


class AgentMessage(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "agent_messages"

    conversation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agent_conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
    )
    parent_message_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agent_messages.id", ondelete="SET NULL"),
    )
    role: Mapped[MessageRole] = mapped_column(
        agent_enum(MessageRole, "agent_message_role"),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[MessageContentType] = mapped_column(
        agent_enum(MessageContentType, "agent_message_content_type"),
        default=MessageContentType.TEXT,
        nullable=False,
    )
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    token_count: Mapped[int | None] = mapped_column(Integer)
    metadata_: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )
    created_by_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    conversation: Mapped[AgentConversation] = relationship(
        back_populates="messages",
        foreign_keys=[conversation_id],
    )
    parent_message: Mapped[AgentMessage | None] = relationship(
        remote_side="AgentMessage.id",
        foreign_keys=[parent_message_id],
    )
    created_by: Mapped[User | None] = relationship(
        foreign_keys=[created_by_id]
    )

    __table_args__ = (
        UniqueConstraint(
            "conversation_id",
            "sequence_number",
            name="uq_agent_messages_conversation_sequence",
        ),
        CheckConstraint(
            "sequence_number >= 1",
            name="agent_message_sequence_positive",
        ),
        CheckConstraint(
            "token_count IS NULL OR token_count >= 0",
            name="agent_message_token_count_nonnegative",
        ),
        CheckConstraint(
            "parent_message_id IS NULL OR parent_message_id != id",
            name="agent_message_not_self_parent",
        ),
        Index(
            "ix_agent_messages_conversation_sequence",
            "conversation_id",
            "sequence_number",
        ),
    )


class AgentRun(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agent_runs"

    conversation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agent_conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agent_definitions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    triggered_by_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    status: Mapped[AgentRunStatus] = mapped_column(
        agent_enum(AgentRunStatus, "agent_run_status"),
        default=AgentRunStatus.QUEUED,
        nullable=False,
    )
    input_message_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
    )
    output_message_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_code: Mapped[str | None] = mapped_column(String(100))
    failure_message: Mapped[str | None] = mapped_column(String(2000))
    model_provider: Mapped[str | None] = mapped_column(String(100))
    model_name: Mapped[str | None] = mapped_column(String(200))
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    total_tokens: Mapped[int | None] = mapped_column(Integer)
    estimated_cost_gbp: Mapped[Decimal | None] = mapped_column(Numeric(18, 6))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    context_snapshot: Mapped[dict[str, object]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    metadata_: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )

    conversation: Mapped[AgentConversation] = relationship(
        back_populates="runs",
        foreign_keys=[conversation_id],
    )
    agent: Mapped[AgentDefinition] = relationship(
        back_populates="runs",
        foreign_keys=[agent_id],
    )
    triggered_by: Mapped[User] = relationship(foreign_keys=[triggered_by_id])
    tool_executions: Mapped[list[AgentToolExecution]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        foreign_keys="AgentToolExecution.run_id",
    )
    approvals: Mapped[list[AgentApproval]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        foreign_keys="AgentApproval.run_id",
    )
    snapshot: Mapped[AgentContextSnapshot | None] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        uselist=False,
    )

    __table_args__ = (
        CheckConstraint(
            "prompt_tokens IS NULL OR prompt_tokens >= 0",
            name="agent_run_prompt_tokens_nonnegative",
        ),
        CheckConstraint(
            "completion_tokens IS NULL OR completion_tokens >= 0",
            name="agent_run_completion_tokens_nonnegative",
        ),
        CheckConstraint(
            "total_tokens IS NULL OR total_tokens >= 0",
            name="agent_run_total_tokens_nonnegative",
        ),
        CheckConstraint(
            "estimated_cost_gbp IS NULL OR estimated_cost_gbp >= 0",
            name="agent_run_cost_nonnegative",
        ),
        CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0",
            name="agent_run_latency_nonnegative",
        ),
        CheckConstraint(
            "started_at IS NULL OR completed_at IS NULL "
            "OR completed_at >= started_at",
            name="agent_run_completion_after_start",
        ),
        Index(
            "ix_agent_runs_conversation_status_created",
            "conversation_id",
            "status",
            "created_at",
        ),
        Index("ix_agent_runs_agent_status", "agent_id", "status"),
    )


class AgentToolDefinition(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agent_tool_definitions"

    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(String(2000), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    risk_level: Mapped[AgentRiskLevel] = mapped_column(
        agent_enum(AgentRiskLevel, "agent_risk_level"),
        nullable=False,
    )
    execution_mode: Mapped[ToolExecutionMode] = mapped_column(
        agent_enum(ToolExecutionMode, "agent_tool_execution_mode"),
        nullable=False,
    )
    requires_approval: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    is_enabled: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    input_schema: Mapped[dict[str, object]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    output_schema: Mapped[dict[str, object]] = mapped_column(
        JSON, default=dict, nullable=False
    )

    executions: Mapped[list[AgentToolExecution]] = relationship(
        back_populates="tool"
    )

    __table_args__ = (
        UniqueConstraint("slug", name="uq_agent_tool_definitions_slug"),
        Index(
            "ix_agent_tool_definitions_slug_enabled",
            "slug",
            "is_enabled",
        ),
    )


class AgentToolExecution(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agent_tool_executions"

    run_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    tool_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agent_tool_definitions.id", ondelete="RESTRICT"),
        nullable=False,
    )
    approval_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
    )
    status: Mapped[ToolExecutionStatus] = mapped_column(
        agent_enum(ToolExecutionStatus, "agent_tool_execution_status"),
        default=ToolExecutionStatus.PENDING,
        nullable=False,
    )
    input_payload: Mapped[dict[str, object]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    output_payload: Mapped[dict[str, object] | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(String(2000))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)

    run: Mapped[AgentRun] = relationship(
        back_populates="tool_executions",
        foreign_keys=[run_id],
    )
    tool: Mapped[AgentToolDefinition] = relationship(
        back_populates="executions"
    )

    __table_args__ = (
        CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name="agent_tool_execution_duration_nonnegative",
        ),
        CheckConstraint(
            "started_at IS NULL OR completed_at IS NULL "
            "OR completed_at >= started_at",
            name="agent_tool_execution_completion_after_start",
        ),
        Index(
            "ix_agent_tool_executions_run_status",
            "run_id",
            "status",
        ),
    )


class AgentApproval(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "agent_approvals"

    run_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    tool_execution_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agent_tool_executions.id", ondelete="SET NULL"),
    )
    requested_by_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    decided_by_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
    )
    action_type: Mapped[str] = mapped_column(String(150), nullable=False)
    risk_level: Mapped[AgentRiskLevel] = mapped_column(
        agent_enum(AgentRiskLevel, "agent_approval_risk_level"),
        nullable=False,
    )
    status: Mapped[AgentApprovalStatus] = mapped_column(
        agent_enum(AgentApprovalStatus, "agent_approval_status"),
        default=AgentApprovalStatus.PENDING,
        nullable=False,
    )
    reason: Mapped[str] = mapped_column(String(2000), nullable=False)
    request_payload: Mapped[dict[str, object]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    decision_note: Mapped[str | None] = mapped_column(String(2000))
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    run: Mapped[AgentRun] = relationship(
        back_populates="approvals",
        foreign_keys=[run_id],
    )
    requested_by: Mapped[User] = relationship(
        foreign_keys=[requested_by_id]
    )
    decided_by: Mapped[User | None] = relationship(
        foreign_keys=[decided_by_id]
    )

    __table_args__ = (
        Index(
            "ix_agent_approvals_status_expires",
            "status",
            "expires_at",
        ),
    )


class AgentMemory(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agent_memories"

    owner_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
    )
    agent_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agent_definitions.id", ondelete="CASCADE"),
    )
    conversation_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agent_conversations.id", ondelete="CASCADE"),
    )
    memory_type: Mapped[AgentMemoryType] = mapped_column(
        agent_enum(AgentMemoryType, "agent_memory_type"),
        nullable=False,
    )
    scope: Mapped[AgentMemoryScope] = mapped_column(
        agent_enum(AgentMemoryScope, "agent_memory_scope"),
        nullable=False,
    )
    key: Mapped[str] = mapped_column(String(200), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    importance: Mapped[int] = mapped_column(Integer, default=3, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    source_message_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agent_messages.id", ondelete="SET NULL"),
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
    )

    __table_args__ = (
        CheckConstraint(
            "importance >= 1 AND importance <= 5",
            name="agent_memory_importance_range",
        ),
        Index(
            "ix_agent_memories_scope_key_active",
            "scope",
            "key",
            "is_active",
        ),
        Index("ix_agent_memories_owner_id", "owner_id"),
        Index("ix_agent_memories_agent_id", "agent_id"),
        Index("ix_agent_memories_conversation_id", "conversation_id"),
    )


class AgentContextSnapshot(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "agent_context_snapshots"

    run_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_context: Mapped[dict[str, object]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    permission_context: Mapped[dict[str, object]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    project_context: Mapped[dict[str, object]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    task_context: Mapped[dict[str, object]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    crm_context: Mapped[dict[str, object]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    outreach_context: Mapped[dict[str, object]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    notification_context: Mapped[dict[str, object]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    memory_context: Mapped[dict[str, object]] = mapped_column(
        JSON, default=dict, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    run: Mapped[AgentRun] = relationship(back_populates="snapshot")

    __table_args__ = (
        UniqueConstraint("run_id", name="uq_agent_context_snapshots_run_id"),
    )


class AgentAttachment(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "agent_attachments"

    conversation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agent_conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    message_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agent_messages.id", ondelete="SET NULL"),
    )
    uploaded_by_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(200), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_key: Mapped[str] = mapped_column(String(1000), nullable=False)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[AgentAttachmentStatus] = mapped_column(
        agent_enum(AgentAttachmentStatus, "agent_attachment_status"),
        default=AgentAttachmentStatus.PENDING,
        nullable=False,
    )
    metadata_: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSON, default=dict, nullable=False
    )

    conversation: Mapped[AgentConversation] = relationship(
        back_populates="attachments"
    )
    uploaded_by: Mapped[User] = relationship(foreign_keys=[uploaded_by_id])

    __table_args__ = (
        CheckConstraint(
            "size_bytes >= 0",
            name="agent_attachment_size_nonnegative",
        ),
        Index(
            "ix_agent_attachments_conversation_status",
            "conversation_id",
            "status",
        ),
    )
