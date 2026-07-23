"""Add dashboard analytics indexes and notifications.

Revision ID: 20260723_0004
Revises: 20260723_0003
Create Date: 2026-07-23

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260723_0004"
down_revision: str | None = "20260723_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "audit_logs",
        sa.Column("project_id", sa.Uuid(), nullable=True),
    )
    op.execute(
        "UPDATE audit_logs SET project_id = entity_id "
        "WHERE entity = 'project'"
    )
    op.execute(
        "UPDATE audit_logs SET project_id = "
        "(SELECT tasks.project_id FROM tasks "
        "WHERE tasks.id = audit_logs.entity_id) "
        "WHERE entity = 'task'"
    )
    op.create_index(
        "ix_audit_logs_project_timestamp",
        "audit_logs",
        ["project_id", "timestamp"],
        unique=False,
    )

    op.create_table(
        "notifications",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "type",
            sa.Enum(
                "task_assigned",
                "task_due_soon",
                "task_overdue",
                "project_status_changed",
                "system",
                name="notification_type",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("message", sa.String(length=1000), nullable=False),
        sa.Column("entity_type", sa.String(length=50), nullable=True),
        sa.Column("entity_id", sa.Uuid(), nullable=True),
        sa.Column("is_read", sa.Boolean(), nullable=False),
        sa.Column(
            "read_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column("dedupe_key", sa.String(length=255), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_notifications_user_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "id",
            name=op.f("pk_notifications"),
        ),
        sa.UniqueConstraint(
            "dedupe_key",
            name=op.f("uq_notifications_dedupe_key"),
        ),
    )
    op.create_index(
        "ix_notifications_user_created",
        "notifications",
        ["user_id", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_notifications_user_read_created",
        "notifications",
        ["user_id", "is_read", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_notifications_expires_at",
        "notifications",
        ["expires_at"],
        unique=False,
    )

    op.create_index(
        op.f("ix_projects_status"),
        "projects",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_projects_created_at",
        "projects",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_projects_archived_at"),
        "projects",
        ["archived_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_tasks_status"),
        "tasks",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_tasks_created_at",
        "tasks",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_tasks_due_date"),
        "tasks",
        ["due_date"],
        unique=False,
    )
    op.create_index(
        "ix_audit_logs_actor_timestamp",
        "audit_logs",
        ["actor_id", "timestamp"],
        unique=False,
    )
    op.create_index(
        "ix_audit_logs_entity_action_timestamp",
        "audit_logs",
        ["entity", "action", "timestamp"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_audit_logs_entity_action_timestamp",
        table_name="audit_logs",
    )
    op.drop_index(
        "ix_audit_logs_actor_timestamp",
        table_name="audit_logs",
    )
    op.drop_index(
        "ix_audit_logs_project_timestamp",
        table_name="audit_logs",
    )
    op.drop_column("audit_logs", "project_id")
    op.drop_index(op.f("ix_tasks_due_date"), table_name="tasks")
    op.drop_index("ix_tasks_created_at", table_name="tasks")
    op.drop_index(op.f("ix_tasks_status"), table_name="tasks")
    op.drop_index(
        op.f("ix_projects_archived_at"),
        table_name="projects",
    )
    op.drop_index("ix_projects_created_at", table_name="projects")
    op.drop_index(op.f("ix_projects_status"), table_name="projects")

    op.drop_index(
        "ix_notifications_expires_at",
        table_name="notifications",
    )
    op.drop_index(
        "ix_notifications_user_read_created",
        table_name="notifications",
    )
    op.drop_index(
        "ix_notifications_user_created",
        table_name="notifications",
    )
    op.drop_table("notifications")
