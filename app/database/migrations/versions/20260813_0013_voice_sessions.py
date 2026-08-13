"""Persist voice sessions in PostgreSQL.

Revision ID: 20260813_0013_voice
Revises: 20260811_0012
Create Date: 2026-08-13
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260813_0013_voice"
down_revision: str | None = "20260811_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "voice_sessions",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("correlation_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(length=30), nullable=False),
        sa.Column("language", sa.String(length=20), nullable=False),
        sa.Column("locale", sa.String(length=35), nullable=False),
        sa.Column("timezone", sa.String(length=100), nullable=False),
        sa.Column("transcript", sa.Text(), nullable=True),
        sa.Column("response_text", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["agent_conversations.id"],
            name=op.f(
                "fk_voice_sessions_conversation_id_agent_conversations"
            ),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["agent_runs.id"],
            name=op.f("fk_voice_sessions_run_id_agent_runs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_voice_sessions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_voice_sessions")),
    )
    op.create_index(
        op.f("ix_voice_sessions_correlation_id"),
        "voice_sessions",
        ["correlation_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_voice_sessions_user_id"),
        "voice_sessions",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_voice_sessions_user_updated",
        "voice_sessions",
        ["user_id", "updated_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("voice_sessions")
