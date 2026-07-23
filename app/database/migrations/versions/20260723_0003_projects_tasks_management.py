"""Add project and task management fields and audit logs.

Revision ID: 20260723_0003
Revises: 20260723_0002
Create Date: 2026-07-23

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260723_0003"
down_revision: str | None = "20260723_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "projects",
        sa.Column("created_by", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "projects",
        sa.Column(
            "archived_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.execute("UPDATE projects SET created_by = owner_id")
    with op.batch_alter_table("projects") as batch:
        batch.alter_column(
            "created_by",
            existing_type=sa.Uuid(),
            nullable=False,
        )
        batch.create_foreign_key(
            op.f("fk_projects_created_by_users"),
            "users",
            ["created_by"],
            ["id"],
            ondelete="RESTRICT",
        )
    op.create_index(
        op.f("ix_projects_created_by"),
        "projects",
        ["created_by"],
        unique=False,
    )
    op.create_index(
        "uq_projects_owner_name_active",
        "projects",
        ["owner_id", sa.text("lower(name)")],
        unique=True,
        postgresql_where=sa.text("archived_at IS NULL"),
        sqlite_where=sa.text("archived_at IS NULL"),
    )

    op.add_column(
        "tasks",
        sa.Column("created_by", sa.Uuid(), nullable=True),
    )
    op.execute(
        "UPDATE tasks SET created_by = "
        "(SELECT projects.created_by FROM projects "
        "WHERE projects.id = tasks.project_id)"
    )
    with op.batch_alter_table("tasks") as batch:
        batch.alter_column(
            "created_by",
            existing_type=sa.Uuid(),
            nullable=False,
        )
        batch.create_foreign_key(
            op.f("fk_tasks_created_by_users"),
            "users",
            ["created_by"],
            ["id"],
            ondelete="RESTRICT",
        )
    op.create_index(
        op.f("ix_tasks_created_by"),
        "tasks",
        ["created_by"],
        unique=False,
    )

    op.create_table(
        "audit_logs",
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column(
            "action",
            sa.Enum(
                "create",
                "update",
                "delete",
                "assignment",
                "status_change",
                name="audit_action",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "entity",
            sa.Enum(
                "project",
                "task",
                name="audit_entity",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column(
            "timestamp",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name=op.f("fk_audit_logs_actor_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )
    op.create_index(
        op.f("ix_audit_logs_actor_id"),
        "audit_logs",
        ["actor_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_audit_logs_entity_id"),
        "audit_logs",
        ["entity_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_audit_logs_timestamp"),
        "audit_logs",
        ["timestamp"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_audit_logs_timestamp"),
        table_name="audit_logs",
    )
    op.drop_index(
        op.f("ix_audit_logs_entity_id"),
        table_name="audit_logs",
    )
    op.drop_index(
        op.f("ix_audit_logs_actor_id"),
        table_name="audit_logs",
    )
    op.drop_table("audit_logs")

    op.drop_index(op.f("ix_tasks_created_by"), table_name="tasks")
    with op.batch_alter_table("tasks") as batch:
        batch.drop_constraint(
            op.f("fk_tasks_created_by_users"),
            type_="foreignkey",
        )
        batch.drop_column("created_by")

    op.drop_index(
        "uq_projects_owner_name_active",
        table_name="projects",
    )
    op.drop_index(
        op.f("ix_projects_created_by"),
        table_name="projects",
    )
    with op.batch_alter_table("projects") as batch:
        batch.drop_constraint(
            op.f("fk_projects_created_by_users"),
            type_="foreignkey",
        )
        batch.drop_column("archived_at")
        batch.drop_column("created_by")
