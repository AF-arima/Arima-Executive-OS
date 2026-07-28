"""Add production identity, workspace, and security foundations.

Revision ID: 20260727_0009
Revises: 20260726_0008
Create Date: 2026-07-27
"""

from collections.abc import Sequence
from datetime import datetime, timezone
from uuid import uuid4

import sqlalchemy as sa
from alembic import op

revision: str = "20260727_0009"
down_revision: str | None = "20260726_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def upgrade() -> None:
    op.create_table(
        "workspaces",
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["users.id"],
            name="fk_workspaces_owner_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_workspaces"),
        sa.UniqueConstraint("owner_id", name="uq_workspaces_owner_id"),
    )
    op.create_index(
        "ix_workspaces_owner_id", "workspaces", ["owner_id"], unique=False
    )
    op.create_table(
        "workspace_memberships",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=50), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            name="fk_workspace_memberships_workspace_id_workspaces",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_workspace_memberships_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_workspace_memberships"),
        sa.UniqueConstraint(
            "workspace_id",
            "user_id",
            name="uq_workspace_memberships_workspace_user",
        ),
    )
    op.create_index(
        "ix_workspace_memberships_workspace_id",
        "workspace_memberships",
        ["workspace_id"],
        unique=False,
    )
    op.create_index(
        "ix_workspace_memberships_user_id",
        "workspace_memberships",
        ["user_id"],
        unique=False,
    )

    op.create_table(
        "security_tokens",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "purpose",
            sa.Enum(
                "email_verification",
                "password_reset",
                "email_change",
                name="security_token_purpose",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("target_email", sa.String(length=320), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("invalidated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_security_tokens_user_id_users",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_security_tokens"),
    )
    for columns, name in (
        (("user_id",), "ix_security_tokens_user_id"),
        (("purpose",), "ix_security_tokens_purpose"),
        (("expires_at",), "ix_security_tokens_expires_at"),
    ):
        op.create_index(name, "security_tokens", list(columns), unique=False)
    op.create_index(
        "ix_security_tokens_token_hash",
        "security_tokens",
        ["token_hash"],
        unique=True,
    )

    op.create_table(
        "security_events",
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("event_metadata", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_security_events_user_id_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_security_events"),
    )
    for columns, name in (
        (("user_id",), "ix_security_events_user_id"),
        (("event_type",), "ix_security_events_event_type"),
        (("occurred_at",), "ix_security_events_occurred_at"),
    ):
        op.create_index(name, "security_events", list(columns), unique=False)

    op.create_table(
        "rate_limit_buckets",
        sa.Column("scope", sa.String(length=100), nullable=False),
        sa.Column("key", sa.String(length=255), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id", name="pk_rate_limit_buckets"),
        sa.UniqueConstraint(
            "scope",
            "key",
            "window_started_at",
            name="uq_rate_limit_buckets_scope_key_window",
        ),
    )
    op.create_index(
        "ix_rate_limit_buckets_scope_key_window",
        "rate_limit_buckets",
        ["scope", "key", "window_started_at"],
        unique=False,
    )

    op.add_column(
        "users",
        sa.Column(
            "failed_login_attempts",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "users", sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "users", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "users", sa.Column("last_login_ip", sa.String(length=64), nullable=True)
    )
    op.create_index("ix_users_locked_until", "users", ["locked_until"], unique=False)

    op.add_column(
        "refresh_token_sessions",
        sa.Column("family_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "refresh_token_sessions", sa.Column("parent_jti", sa.String(length=36), nullable=True)
    )
    op.add_column(
        "refresh_token_sessions",
        sa.Column(
            "is_persistent",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )
    op.add_column(
        "refresh_token_sessions", sa.Column("user_agent", sa.String(length=512), nullable=True)
    )
    op.add_column(
        "refresh_token_sessions", sa.Column("ip_address", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "refresh_token_sessions", sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "refresh_token_sessions", sa.Column("revoked_reason", sa.String(length=100), nullable=True)
    )

    bind = op.get_bind()
    now = datetime.now(timezone.utc)
    user_rows = bind.execute(
        sa.text("SELECT id, first_name, last_name FROM users")
    ).mappings()
    for row in user_rows:
        workspace_id = uuid4()
        owner_id = row["id"]
        first_name = str(row["first_name"]).strip() or "Arima"
        last_name = str(row["last_name"]).strip() or "Workspace"
        suffix = " Workspace"
        full_name = f"{first_name} {last_name}".strip() or "Arima"
        workspace_name = f"{full_name[: 160 - len(suffix)]}{suffix}"
        bind.execute(
            sa.text(
                "INSERT INTO workspaces "
                "(id, name, owner_id, created_at, updated_at) "
                "VALUES (:id, :name, :owner_id, :created_at, :updated_at)"
            ),
            {
                "id": str(workspace_id),
                "name": workspace_name,
                "owner_id": str(owner_id),
                "created_at": now,
                "updated_at": now,
            },
        )
        bind.execute(
            sa.text(
                "INSERT INTO workspace_memberships "
                "(id, workspace_id, user_id, role, created_at, updated_at) "
                "VALUES (:id, :workspace_id, :user_id, :role, :created_at, :updated_at)"
            ),
            {
                "id": str(uuid4()),
                "workspace_id": str(workspace_id),
                "user_id": str(owner_id),
                "role": "owner",
                "created_at": now,
                "updated_at": now,
            },
        )

    refresh_rows = bind.execute(
        sa.text("SELECT id FROM refresh_token_sessions WHERE family_id IS NULL")
    ).mappings()
    for row in refresh_rows:
        bind.execute(
            sa.text(
                "UPDATE refresh_token_sessions "
                "SET family_id = :family_id WHERE id = :id"
            ),
            {"family_id": str(uuid4()), "id": str(row["id"])},
        )
    with op.batch_alter_table("refresh_token_sessions") as batch:
        batch.alter_column(
            "family_id",
            existing_type=sa.Uuid(),
            nullable=False,
        )
        batch.alter_column(
            "is_persistent",
            existing_type=sa.Boolean(),
            server_default=None,
        )
    with op.batch_alter_table("users") as batch:
        batch.alter_column(
            "failed_login_attempts",
            existing_type=sa.Integer(),
            server_default=None,
        )
    op.create_index(
        "ix_refresh_token_sessions_family_id",
        "refresh_token_sessions",
        ["family_id"],
        unique=False,
    )
    op.create_index(
        "ix_refresh_token_sessions_user_family_active",
        "refresh_token_sessions",
        ["user_id", "family_id", "revoked_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_refresh_token_sessions_user_family_active",
        table_name="refresh_token_sessions",
    )
    op.drop_index(
        "ix_refresh_token_sessions_family_id",
        table_name="refresh_token_sessions",
    )
    with op.batch_alter_table("refresh_token_sessions") as batch:
        batch.drop_column("revoked_reason")
        batch.drop_column("last_used_at")
        batch.drop_column("ip_address")
        batch.drop_column("user_agent")
        batch.drop_column("is_persistent")
        batch.drop_column("parent_jti")
        batch.drop_column("family_id")

    op.drop_index("ix_users_locked_until", table_name="users")
    with op.batch_alter_table("users") as batch:
        batch.drop_column("last_login_ip")
        batch.drop_column("last_login_at")
        batch.drop_column("locked_until")
        batch.drop_column("failed_login_attempts")

    op.drop_index(
        "ix_rate_limit_buckets_scope_key_window",
        table_name="rate_limit_buckets",
    )
    op.drop_table("rate_limit_buckets")

    for name in (
        "ix_security_events_occurred_at",
        "ix_security_events_event_type",
        "ix_security_events_user_id",
    ):
        op.drop_index(name, table_name="security_events")
    op.drop_table("security_events")

    for name in (
        "ix_security_tokens_expires_at",
        "ix_security_tokens_token_hash",
        "ix_security_tokens_purpose",
        "ix_security_tokens_user_id",
    ):
        op.drop_index(name, table_name="security_tokens")
    op.drop_table("security_tokens")

    op.drop_index(
        "ix_workspace_memberships_user_id",
        table_name="workspace_memberships",
    )
    op.drop_index(
        "ix_workspace_memberships_workspace_id",
        table_name="workspace_memberships",
    )
    op.drop_table("workspace_memberships")
    op.drop_index("ix_workspaces_owner_id", table_name="workspaces")
    op.drop_table("workspaces")
