"""Create refresh token sessions.

Revision ID: 20260723_0002
Revises: 20260723_0001
Create Date: 2026-07-23

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260723_0002"
down_revision: str | None = "20260723_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "refresh_token_sessions",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("token_jti", sa.String(length=36), nullable=False),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "revoked_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_refresh_token_sessions_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("pk_refresh_token_sessions"),
        ),
    )
    op.create_index(
        op.f("ix_refresh_token_sessions_token_jti"),
        "refresh_token_sessions",
        ["token_jti"],
        unique=True,
    )
    op.create_index(
        op.f("ix_refresh_token_sessions_user_id"),
        "refresh_token_sessions",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_refresh_token_sessions_user_id"),
        table_name="refresh_token_sessions",
    )
    op.drop_index(
        op.f("ix_refresh_token_sessions_token_jti"),
        table_name="refresh_token_sessions",
    )
    op.drop_table("refresh_token_sessions")
