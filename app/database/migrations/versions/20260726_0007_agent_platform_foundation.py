"""Add the Arima Agent Platform domain foundation.

Revision ID: 20260726_0007
Revises: 20260723_0006
Create Date: 2026-07-26
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "20260726_0007"
down_revision: str | None = "20260723_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _enum(name: str, *values: str) -> sa.Enum:
    return sa.Enum(
        *values,
        name=name,
        native_enum=False,
        create_constraint=True,
    )


def _identity_columns() -> tuple[sa.Column, ...]:
    return (sa.Column("id", sa.Uuid(), nullable=False),)


def _timestamp_columns() -> tuple[sa.Column, ...]:
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def upgrade() -> None:
    op.create_table(
        "agent_definitions",
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=True),
        sa.Column("system_instructions", sa.Text(), nullable=False),
        sa.Column(
            "status",
            _enum(
                "agent_status",
                "draft",
                "active",
                "disabled",
                "archived",
            ),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=False),
        sa.Column(
            "archived_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        *_identity_columns(),
        *_timestamp_columns(),
        sa.CheckConstraint(
            "version > 0",
            name="agent_version_positive",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name="fk_agent_definitions_created_by_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name="pk_agent_definitions",
        ),
        sa.UniqueConstraint(
            "slug",
            name="uq_agent_definitions_slug",
        ),
    )
    op.create_index(
        "ix_agent_definitions_status",
        "agent_definitions",
        ["status"],
        unique=False,
    )
    op.create_index(
        "uq_agent_definitions_active_default",
        "agent_definitions",
        ["is_default"],
        unique=True,
        postgresql_where=sa.text(
            "is_default AND status = 'active' AND archived_at IS NULL"
        ),
        sqlite_where=sa.text(
            "is_default = 1 AND status = 'active' AND archived_at IS NULL"
        ),
    )

    op.create_table(
        "agent_tool_definitions",
        sa.Column("slug", sa.String(length=120), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=False),
        sa.Column("category", sa.String(length=100), nullable=False),
        sa.Column(
            "risk_level",
            _enum(
                "agent_risk_level",
                "low",
                "medium",
                "high",
                "critical",
            ),
            nullable=False,
        ),
        sa.Column(
            "execution_mode",
            _enum(
                "agent_tool_execution_mode",
                "internal",
                "provider",
                "deferred",
            ),
            nullable=False,
        ),
        sa.Column("requires_approval", sa.Boolean(), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.Column("input_schema", sa.JSON(), nullable=False),
        sa.Column("output_schema", sa.JSON(), nullable=False),
        *_identity_columns(),
        *_timestamp_columns(),
        sa.PrimaryKeyConstraint(
            "id",
            name="pk_agent_tool_definitions",
        ),
        sa.UniqueConstraint(
            "slug",
            name="uq_agent_tool_definitions_slug",
        ),
    )
    op.create_index(
        "ix_agent_tool_definitions_slug_enabled",
        "agent_tool_definitions",
        ["slug", "is_enabled"],
        unique=False,
    )

    op.create_table(
        "agent_conversations",
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column(
            "status",
            _enum(
                "agent_conversation_status",
                "active",
                "archived",
                "closed",
            ),
            nullable=False,
        ),
        sa.Column(
            "priority",
            _enum(
                "agent_conversation_priority",
                "low",
                "normal",
                "high",
                "urgent",
            ),
            nullable=False,
        ),
        sa.Column("pinned", sa.Boolean(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column(
            "last_message_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "archived_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        *_identity_columns(),
        *_timestamp_columns(),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agent_definitions.id"],
            name="fk_agent_conversations_agent_id_agent_definitions",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name="fk_agent_conversations_owner_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name="pk_agent_conversations",
        ),
    )
    op.create_index(
        "ix_agent_conversations_agent_status",
        "agent_conversations",
        ["agent_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_agent_conversations_owner_status_updated",
        "agent_conversations",
        ["owner_id", "status", "updated_at"],
        unique=False,
    )

    op.create_table(
        "agent_runs",
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("agent_id", sa.Uuid(), nullable=False),
        sa.Column("triggered_by_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            _enum(
                "agent_run_status",
                "queued",
                "running",
                "waiting_for_approval",
                "completed",
                "failed",
                "cancelled",
            ),
            nullable=False,
        ),
        sa.Column("input_message_id", sa.Uuid(), nullable=True),
        sa.Column("output_message_id", sa.Uuid(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("failure_code", sa.String(length=100), nullable=True),
        sa.Column(
            "failure_message",
            sa.String(length=2000),
            nullable=True,
        ),
        sa.Column("model_provider", sa.String(length=100), nullable=True),
        sa.Column("model_name", sa.String(length=200), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=True),
        sa.Column("completion_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column(
            "estimated_cost_gbp",
            sa.Numeric(precision=18, scale=6),
            nullable=True,
        ),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("context_snapshot", sa.JSON(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        *_identity_columns(),
        *_timestamp_columns(),
        sa.CheckConstraint(
            "prompt_tokens IS NULL OR prompt_tokens >= 0",
            name="agent_run_prompt_tokens_nonnegative",
        ),
        sa.CheckConstraint(
            "completion_tokens IS NULL OR completion_tokens >= 0",
            name="agent_run_completion_tokens_nonnegative",
        ),
        sa.CheckConstraint(
            "total_tokens IS NULL OR total_tokens >= 0",
            name="agent_run_total_tokens_nonnegative",
        ),
        sa.CheckConstraint(
            "estimated_cost_gbp IS NULL OR estimated_cost_gbp >= 0",
            name="agent_run_cost_nonnegative",
        ),
        sa.CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0",
            name="agent_run_latency_nonnegative",
        ),
        sa.CheckConstraint(
            "started_at IS NULL OR completed_at IS NULL "
            "OR completed_at >= started_at",
            name="agent_run_completion_after_start",
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agent_definitions.id"],
            name="fk_agent_runs_agent_id_agent_definitions",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["agent_conversations.id"],
            name="fk_agent_runs_conversation_id_agent_conversations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["triggered_by_id"],
            ["users.id"],
            name="fk_agent_runs_triggered_by_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_runs"),
    )
    op.create_index(
        "ix_agent_runs_agent_status",
        "agent_runs",
        ["agent_id", "status"],
        unique=False,
    )
    op.create_index(
        "ix_agent_runs_conversation_status_created",
        "agent_runs",
        ["conversation_id", "status", "created_at"],
        unique=False,
    )

    op.create_table(
        "agent_messages",
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("parent_message_id", sa.Uuid(), nullable=True),
        sa.Column(
            "role",
            _enum(
                "agent_message_role",
                "system",
                "user",
                "assistant",
                "tool",
                "approval",
            ),
            nullable=False,
        ),
        sa.Column(
            "content_type",
            _enum(
                "agent_message_content_type",
                "text",
                "json",
                "markdown",
            ),
            nullable=False,
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        *_identity_columns(),
        sa.CheckConstraint(
            "sequence_number >= 1",
            name="agent_message_sequence_positive",
        ),
        sa.CheckConstraint(
            "token_count IS NULL OR token_count >= 0",
            name="agent_message_token_count_nonnegative",
        ),
        sa.CheckConstraint(
            "parent_message_id IS NULL OR parent_message_id != id",
            name="agent_message_not_self_parent",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["agent_conversations.id"],
            name="fk_agent_messages_conversation_id_agent_conversations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name="fk_agent_messages_created_by_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["parent_message_id"],
            ["agent_messages.id"],
            name="fk_agent_messages_parent_message_id_agent_messages",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            name="fk_agent_messages_run_id_agent_runs",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_messages"),
        sa.UniqueConstraint(
            "conversation_id",
            "sequence_number",
            name="uq_agent_messages_conversation_sequence",
        ),
    )
    op.create_index(
        "ix_agent_messages_conversation_sequence",
        "agent_messages",
        ["conversation_id", "sequence_number"],
        unique=False,
    )

    op.create_table(
        "agent_tool_executions",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("tool_id", sa.Uuid(), nullable=False),
        sa.Column("approval_id", sa.Uuid(), nullable=True),
        sa.Column(
            "status",
            _enum(
                "agent_tool_execution_status",
                "pending",
                "running",
                "succeeded",
                "failed",
                "cancelled",
            ),
            nullable=False,
        ),
        sa.Column("input_payload", sa.JSON(), nullable=False),
        sa.Column("output_payload", sa.JSON(), nullable=True),
        sa.Column("error_code", sa.String(length=100), nullable=True),
        sa.Column(
            "error_message",
            sa.String(length=2000),
            nullable=True,
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        *_identity_columns(),
        *_timestamp_columns(),
        sa.CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name="agent_tool_execution_duration_nonnegative",
        ),
        sa.CheckConstraint(
            "started_at IS NULL OR completed_at IS NULL "
            "OR completed_at >= started_at",
            name="agent_tool_execution_completion_after_start",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            name="fk_agent_tool_executions_run_id_agent_runs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tool_id"],
            ["agent_tool_definitions.id"],
            name=(
                "fk_agent_tool_executions_tool_id_"
                "agent_tool_definitions"
            ),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name="pk_agent_tool_executions",
        ),
    )
    op.create_index(
        "ix_agent_tool_executions_run_status",
        "agent_tool_executions",
        ["run_id", "status"],
        unique=False,
    )

    op.create_table(
        "agent_approvals",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("tool_execution_id", sa.Uuid(), nullable=True),
        sa.Column("requested_by_id", sa.Uuid(), nullable=False),
        sa.Column("decided_by_id", sa.Uuid(), nullable=True),
        sa.Column("action_type", sa.String(length=150), nullable=False),
        sa.Column(
            "risk_level",
            _enum(
                "agent_approval_risk_level",
                "low",
                "medium",
                "high",
                "critical",
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            _enum(
                "agent_approval_status",
                "pending",
                "approved",
                "rejected",
                "expired",
                "cancelled",
            ),
            nullable=False,
        ),
        sa.Column("reason", sa.String(length=2000), nullable=False),
        sa.Column("request_payload", sa.JSON(), nullable=False),
        sa.Column(
            "decision_note",
            sa.String(length=2000),
            nullable=True,
        ),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "decided_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        *_identity_columns(),
        sa.ForeignKeyConstraint(
            ["decided_by_id"],
            ["users.id"],
            name="fk_agent_approvals_decided_by_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_id"],
            ["users.id"],
            name="fk_agent_approvals_requested_by_id_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            name="fk_agent_approvals_run_id_agent_runs",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tool_execution_id"],
            ["agent_tool_executions.id"],
            name=(
                "fk_agent_approvals_tool_execution_id_"
                "agent_tool_executions"
            ),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_approvals"),
    )
    op.create_index(
        "ix_agent_approvals_status_expires",
        "agent_approvals",
        ["status", "expires_at"],
        unique=False,
    )

    op.create_table(
        "agent_memories",
        sa.Column("owner_id", sa.Uuid(), nullable=True),
        sa.Column("agent_id", sa.Uuid(), nullable=True),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column(
            "memory_type",
            _enum(
                "agent_memory_type",
                "preference",
                "fact",
                "decision",
                "policy",
                "summary",
                "instruction",
            ),
            nullable=False,
        ),
        sa.Column(
            "scope",
            _enum(
                "agent_memory_scope",
                "user",
                "agent",
                "conversation",
                "organisation",
            ),
            nullable=False,
        ),
        sa.Column("key", sa.String(length=200), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("importance", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("source_message_id", sa.Uuid(), nullable=True),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        *_identity_columns(),
        *_timestamp_columns(),
        sa.CheckConstraint(
            "importance >= 1 AND importance <= 5",
            name="agent_memory_importance_range",
        ),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agent_definitions.id"],
            name="fk_agent_memories_agent_id_agent_definitions",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["agent_conversations.id"],
            name=(
                "fk_agent_memories_conversation_id_"
                "agent_conversations"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"],
            ["users.id"],
            name="fk_agent_memories_created_by_id_users",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name="fk_agent_memories_owner_id_users",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_message_id"],
            ["agent_messages.id"],
            name=(
                "fk_agent_memories_source_message_id_"
                "agent_messages"
            ),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_memories"),
    )
    op.create_index(
        "ix_agent_memories_agent_id",
        "agent_memories",
        ["agent_id"],
        unique=False,
    )
    op.create_index(
        "ix_agent_memories_conversation_id",
        "agent_memories",
        ["conversation_id"],
        unique=False,
    )
    op.create_index(
        "ix_agent_memories_owner_id",
        "agent_memories",
        ["owner_id"],
        unique=False,
    )
    op.create_index(
        "ix_agent_memories_scope_key_active",
        "agent_memories",
        ["scope", "key", "is_active"],
        unique=False,
    )

    op.create_table(
        "agent_context_snapshots",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("user_context", sa.JSON(), nullable=False),
        sa.Column("permission_context", sa.JSON(), nullable=False),
        sa.Column("project_context", sa.JSON(), nullable=False),
        sa.Column("task_context", sa.JSON(), nullable=False),
        sa.Column("crm_context", sa.JSON(), nullable=False),
        sa.Column("outreach_context", sa.JSON(), nullable=False),
        sa.Column("notification_context", sa.JSON(), nullable=False),
        sa.Column("memory_context", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        *_identity_columns(),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            name="fk_agent_context_snapshots_run_id_agent_runs",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name="pk_agent_context_snapshots",
        ),
        sa.UniqueConstraint(
            "run_id",
            name="uq_agent_context_snapshots_run_id",
        ),
    )

    op.create_table(
        "agent_attachments",
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=True),
        sa.Column("uploaded_by_id", sa.Uuid(), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=200), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(length=1000), nullable=False),
        sa.Column(
            "checksum_sha256",
            sa.String(length=64),
            nullable=True,
        ),
        sa.Column(
            "status",
            _enum(
                "agent_attachment_status",
                "pending",
                "available",
                "failed",
                "deleted",
            ),
            nullable=False,
        ),
        sa.Column("metadata", sa.JSON(), nullable=False),
        *_identity_columns(),
        *_timestamp_columns(),
        sa.CheckConstraint(
            "size_bytes >= 0",
            name="agent_attachment_size_nonnegative",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["agent_conversations.id"],
            name=(
                "fk_agent_attachments_conversation_id_"
                "agent_conversations"
            ),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["agent_messages.id"],
            name="fk_agent_attachments_message_id_agent_messages",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["uploaded_by_id"],
            ["users.id"],
            name="fk_agent_attachments_uploaded_by_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_agent_attachments"),
    )
    op.create_index(
        "ix_agent_attachments_conversation_status",
        "agent_attachments",
        ["conversation_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_attachments_conversation_status",
        table_name="agent_attachments",
    )
    op.drop_table("agent_attachments")
    op.drop_table("agent_context_snapshots")
    op.drop_index(
        "ix_agent_memories_scope_key_active",
        table_name="agent_memories",
    )
    op.drop_index(
        "ix_agent_memories_owner_id",
        table_name="agent_memories",
    )
    op.drop_index(
        "ix_agent_memories_conversation_id",
        table_name="agent_memories",
    )
    op.drop_index(
        "ix_agent_memories_agent_id",
        table_name="agent_memories",
    )
    op.drop_table("agent_memories")
    op.drop_index(
        "ix_agent_approvals_status_expires",
        table_name="agent_approvals",
    )
    op.drop_table("agent_approvals")
    op.drop_index(
        "ix_agent_tool_executions_run_status",
        table_name="agent_tool_executions",
    )
    op.drop_table("agent_tool_executions")
    op.drop_index(
        "ix_agent_messages_conversation_sequence",
        table_name="agent_messages",
    )
    op.drop_table("agent_messages")
    op.drop_index(
        "ix_agent_runs_conversation_status_created",
        table_name="agent_runs",
    )
    op.drop_index(
        "ix_agent_runs_agent_status",
        table_name="agent_runs",
    )
    op.drop_table("agent_runs")
    op.drop_index(
        "ix_agent_conversations_owner_status_updated",
        table_name="agent_conversations",
    )
    op.drop_index(
        "ix_agent_conversations_agent_status",
        table_name="agent_conversations",
    )
    op.drop_table("agent_conversations")
    op.drop_index(
        "ix_agent_tool_definitions_slug_enabled",
        table_name="agent_tool_definitions",
    )
    op.drop_table("agent_tool_definitions")
    op.drop_index(
        "uq_agent_definitions_active_default",
        table_name="agent_definitions",
    )
    op.drop_index(
        "ix_agent_definitions_status",
        table_name="agent_definitions",
    )
    op.drop_table("agent_definitions")
