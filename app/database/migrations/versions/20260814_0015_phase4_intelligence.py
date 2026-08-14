"""Add governed Phase 4 intelligence and Telegram foundations.

Revision ID: 20260814_0015_ai
Revises: 20260813_0014_market
Create Date: 2026-08-14
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260814_0015_ai"
down_revision: str | None = "20260813_0014_market"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def upgrade() -> None:
    op.create_table(
        "workspace_agent_grants",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("granted_by_id", sa.Uuid(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_workspace_agent_grants_workspace_id_workspaces",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agent_definitions.id"],
            name="fk_workspace_agent_grants_agent_id_agent_definitions",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["granted_by_id"],
            ["users.id"],
            name="fk_workspace_agent_grants_granted_by_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_workspace_agent_grants"),
        sa.UniqueConstraint(
            "workspace_id",
            "agent_id",
            name="uq_workspace_agent_grants_workspace_agent",
        ),
    )
    op.create_index(
        "ix_workspace_agent_grants_workspace_active",
        "workspace_agent_grants",
        ["workspace_id", "revoked_at"],
        unique=False,
    )

    op.create_table(
        "ai_workspace_runs",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("channel", sa.String(length=50), nullable=False),
        sa.Column("correlation_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_ai_workspace_runs_workspace_id_workspaces",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            name="fk_ai_workspace_runs_run_id_agent_runs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_ai_workspace_runs_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ai_workspace_runs"),
        sa.UniqueConstraint("run_id", name="uq_ai_workspace_runs_run_id"),
        sa.UniqueConstraint(
            "correlation_id",
            name="uq_ai_workspace_runs_correlation_id",
        ),
    )
    op.create_index(
        "ix_ai_workspace_runs_workspace_created",
        "ai_workspace_runs",
        ["workspace_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_ai_workspace_runs_user_id",
        "ai_workspace_runs",
        ["user_id"],
        unique=False,
    )

    op.create_table(
        "knowledge_sources",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("source_type", sa.String(length=50), nullable=False),
        sa.Column("external_id", sa.String(length=300), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("source_uri", sa.String(length=2000), nullable=True),
        sa.Column("freshness_required", sa.Boolean(), nullable=False),
        sa.Column("max_age_seconds", sa.Integer(), nullable=True),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_knowledge_sources_workspace_id_workspaces",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_sources"),
        sa.UniqueConstraint(
            "workspace_id",
            "source_type",
            "external_id",
            name="uq_knowledge_sources_workspace_external",
        ),
    )
    op.create_index(
        "ix_knowledge_sources_workspace_enabled",
        "knowledge_sources",
        ["workspace_id", "is_enabled"],
        unique=False,
    )

    op.create_table(
        "knowledge_documents",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("external_id", sa.String(length=500), nullable=False),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("source_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "ingested",
                "failed",
                name="knowledge_document_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.String(length=2000), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_knowledge_documents_workspace_id_workspaces",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_id"],
            ["knowledge_sources.id"],
            name="fk_knowledge_documents_source_id_knowledge_sources",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_documents"),
        sa.UniqueConstraint(
            "source_id",
            "external_id",
            "content_hash",
            name="uq_knowledge_documents_source_version",
        ),
    )
    op.create_index(
        "ix_knowledge_documents_workspace_observed",
        "knowledge_documents",
        ["workspace_id", "source_observed_at"],
        unique=False,
    )

    op.create_table(
        "knowledge_chunks",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_knowledge_chunks_workspace_id_workspaces",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["knowledge_documents.id"],
            name="fk_knowledge_chunks_document_id_knowledge_documents",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_knowledge_chunks"),
        sa.UniqueConstraint(
            "document_id",
            "ordinal",
            name="uq_knowledge_chunks_document_ordinal",
        ),
    )
    op.create_index(
        "ix_knowledge_chunks_workspace_document",
        "knowledge_chunks",
        ["workspace_id", "document_id"],
        unique=False,
    )

    op.create_table(
        "ai_retrieved_contexts",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_id", sa.Uuid(), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_ai_retrieved_contexts_workspace_id_workspaces",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            name="fk_ai_retrieved_contexts_run_id_agent_runs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["chunk_id"],
            ["knowledge_chunks.id"],
            name="fk_ai_retrieved_contexts_chunk_id_knowledge_chunks",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ai_retrieved_contexts"),
        sa.UniqueConstraint(
            "run_id",
            "chunk_id",
            name="uq_ai_retrieved_contexts_run_chunk",
        ),
    )
    op.create_index(
        "ix_ai_retrieved_contexts_run_rank",
        "ai_retrieved_contexts",
        ["run_id", "rank"],
        unique=False,
    )
    op.create_index(
        "ix_ai_retrieved_contexts_workspace_retrieved",
        "ai_retrieved_contexts",
        ["workspace_id", "retrieved_at"],
        unique=False,
    )

    op.create_table(
        "telegram_identities",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("telegram_user_id", sa.String(length=100), nullable=False),
        sa.Column("telegram_chat_id", sa.String(length=100), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "verified",
                "revoked",
                name="telegram_identity_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("verified_by_id", sa.Uuid(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_telegram_identities_workspace_id_workspaces",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_telegram_identities_user_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["verified_by_id"],
            ["users.id"],
            name="fk_telegram_identities_verified_by_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_telegram_identities"),
        sa.UniqueConstraint(
            "telegram_user_id",
            name="uq_telegram_identities_telegram_user_id",
        ),
        sa.UniqueConstraint(
            "telegram_user_id",
            "telegram_chat_id",
            name="uq_telegram_identities_user_chat",
        ),
    )
    op.create_index(
        "ix_telegram_identities_workspace_status",
        "telegram_identities",
        ["workspace_id", "status"],
        unique=False,
    )

    op.create_table(
        "telegram_messages",
        sa.Column("workspace_id", sa.Uuid(), nullable=True),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("identity_id", sa.Uuid(), nullable=True),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("update_id", sa.BigInteger(), nullable=False),
        sa.Column("chat_id", sa.String(length=100), nullable=False),
        sa.Column("telegram_user_id", sa.String(length=100), nullable=False),
        sa.Column("incoming_message_id", sa.String(length=100), nullable=False),
        sa.Column("outgoing_message_id", sa.String(length=100), nullable=True),
        sa.Column("incoming_text", sa.Text(), nullable=False),
        sa.Column("outgoing_text", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "received",
                "processing",
                "completed",
                "failed",
                name="telegram_processing_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column("error_message", sa.String(length=2000), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_telegram_messages_workspace_id_workspaces",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_telegram_messages_user_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["identity_id"],
            ["telegram_identities.id"],
            name="fk_telegram_messages_identity_id_telegram_identities",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["agent_conversations.id"],
            name="fk_telegram_messages_conversation_id_agent_conversations",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            name="fk_telegram_messages_run_id_agent_runs",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_telegram_messages"),
        sa.UniqueConstraint("update_id", name="uq_telegram_messages_update_id"),
    )
    op.create_index(
        "ix_telegram_messages_workspace_received",
        "telegram_messages",
        ["workspace_id", "received_at"],
        unique=False,
    )
    op.create_index(
        "ix_telegram_messages_chat_message",
        "telegram_messages",
        ["chat_id", "incoming_message_id"],
        unique=False,
    )
    op.create_index(
        "ix_telegram_messages_status",
        "telegram_messages",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("telegram_messages")
    op.drop_table("telegram_identities")
    op.drop_table("ai_retrieved_contexts")
    op.drop_table("knowledge_chunks")
    op.drop_table("knowledge_documents")
    op.drop_table("knowledge_sources")
    op.drop_table("ai_workspace_runs")
    op.drop_table("workspace_agent_grants")
