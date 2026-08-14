from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum as SQLAlchemyEnum,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models.base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    utc_now,
)


class KnowledgeDocumentStatus(str, Enum):
    INGESTED = "ingested"
    FAILED = "failed"


class TelegramIdentityStatus(str, Enum):
    VERIFIED = "verified"
    REVOKED = "revoked"


class TelegramProcessingStatus(str, Enum):
    RECEIVED = "received"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


def intelligence_enum(enum_type: type[Enum], name: str) -> SQLAlchemyEnum:
    return SQLAlchemyEnum(
        enum_type,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        values_callable=lambda enum: [item.value for item in enum],
    )


class WorkspaceAgentGrant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Explicit workspace authorization for a platform agent."""

    __tablename__ = "workspace_agent_grants"

    workspace_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    agent_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agent_definitions.id", ondelete="CASCADE"),
        nullable=False,
    )
    granted_by_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "agent_id",
            name="uq_workspace_agent_grants_workspace_agent",
        ),
        Index(
            "ix_workspace_agent_grants_workspace_active",
            "workspace_id",
            "revoked_at",
        ),
    )


class AIWorkspaceRun(UUIDPrimaryKeyMixin, Base):
    """Workspace binding for an existing durable AgentRun."""

    __tablename__ = "ai_workspace_runs"

    workspace_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    channel: Mapped[str] = mapped_column(String(50), nullable=False)
    correlation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), nullable=False, unique=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    __table_args__ = (
        Index(
            "ix_ai_workspace_runs_workspace_created",
            "workspace_id",
            "created_at",
        ),
        Index("ix_ai_workspace_runs_user_id", "user_id"),
    )


class KnowledgeSource(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "knowledge_sources"

    workspace_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    external_id: Mapped[str] = mapped_column(String(300), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    source_uri: Mapped[str | None] = mapped_column(String(2000))
    freshness_required: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    max_age_seconds: Mapped[int | None] = mapped_column(Integer)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "source_type",
            "external_id",
            name="uq_knowledge_sources_workspace_external",
        ),
        Index(
            "ix_knowledge_sources_workspace_enabled",
            "workspace_id",
            "is_enabled",
        ),
    )


class KnowledgeDocument(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "knowledge_documents"

    workspace_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("knowledge_sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    external_id: Mapped[str] = mapped_column(String(500), nullable=False)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ingested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provenance: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    status: Mapped[KnowledgeDocumentStatus] = mapped_column(
        intelligence_enum(KnowledgeDocumentStatus, "knowledge_document_status"),
        nullable=False,
    )
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(String(2000))

    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "external_id",
            "content_hash",
            name="uq_knowledge_documents_source_version",
        ),
        Index(
            "ix_knowledge_documents_workspace_observed",
            "workspace_id",
            "source_observed_at",
        ),
    )


class KnowledgeChunk(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "knowledge_chunks"

    workspace_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    document_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "ordinal",
            name="uq_knowledge_chunks_document_ordinal",
        ),
        Index(
            "ix_knowledge_chunks_workspace_document",
            "workspace_id",
            "document_id",
        ),
    )


class AIRetrievedContext(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "ai_retrieved_contexts"

    workspace_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    run_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    chunk_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("knowledge_chunks.id", ondelete="RESTRICT"),
        nullable=False,
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    retrieved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    source_observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    provenance: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "run_id",
            "chunk_id",
            name="uq_ai_retrieved_contexts_run_chunk",
        ),
        Index(
            "ix_ai_retrieved_contexts_run_rank",
            "run_id",
            "rank",
        ),
        Index(
            "ix_ai_retrieved_contexts_workspace_retrieved",
            "workspace_id",
            "retrieved_at",
        ),
    )


class TelegramIdentity(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "telegram_identities"

    workspace_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    telegram_user_id: Mapped[str] = mapped_column(
        String(100), nullable=False, unique=True
    )
    telegram_chat_id: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[TelegramIdentityStatus] = mapped_column(
        intelligence_enum(TelegramIdentityStatus, "telegram_identity_status"),
        nullable=False,
    )
    verified_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    verified_by_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "telegram_user_id",
            "telegram_chat_id",
            name="uq_telegram_identities_user_chat",
        ),
        Index(
            "ix_telegram_identities_workspace_status",
            "workspace_id",
            "status",
        ),
    )


class TelegramMessage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "telegram_messages"

    workspace_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="SET NULL"),
    )
    user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
    )
    identity_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("telegram_identities.id", ondelete="SET NULL"),
    )
    conversation_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agent_conversations.id", ondelete="SET NULL"),
    )
    run_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("agent_runs.id", ondelete="SET NULL"),
    )
    update_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    chat_id: Mapped[str] = mapped_column(String(100), nullable=False)
    telegram_user_id: Mapped[str] = mapped_column(String(100), nullable=False)
    incoming_message_id: Mapped[str] = mapped_column(String(100), nullable=False)
    outgoing_message_id: Mapped[str | None] = mapped_column(String(100))
    incoming_text: Mapped[str] = mapped_column(Text, nullable=False)
    outgoing_text: Mapped[str | None] = mapped_column(Text)
    status: Mapped[TelegramProcessingStatus] = mapped_column(
        intelligence_enum(TelegramProcessingStatus, "telegram_processing_status"),
        nullable=False,
    )
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    processing_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(String(2000))

    __table_args__ = (
        Index(
            "ix_telegram_messages_workspace_received",
            "workspace_id",
            "received_at",
        ),
        Index(
            "ix_telegram_messages_chat_message",
            "chat_id",
            "incoming_message_id",
        ),
        Index("ix_telegram_messages_status", "status"),
    )
