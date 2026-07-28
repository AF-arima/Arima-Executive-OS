"""Scope CRM pipeline uniqueness and defaults to their owning user.

Revision ID: 20260728_0010
Revises: 20260727_0009
Create Date: 2026-07-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260728_0010"
down_revision: str | None = "20260727_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("uq_crm_pipelines_default_active", table_name="crm_pipelines")
    with op.batch_alter_table("crm_pipelines") as batch:
        batch.drop_constraint("uq_crm_pipelines_name", type_="unique")
        batch.create_unique_constraint(
            "uq_crm_pipelines_creator_name",
            ["created_by", "name"],
        )
    op.create_index(
        "uq_crm_pipelines_creator_default_active",
        "crm_pipelines",
        ["created_by", "is_default"],
        unique=True,
        postgresql_where=sa.text("is_default AND is_active"),
        sqlite_where=sa.text("is_default = 1 AND is_active = 1"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_crm_pipelines_creator_default_active",
        table_name="crm_pipelines",
    )
    with op.batch_alter_table("crm_pipelines") as batch:
        batch.drop_constraint("uq_crm_pipelines_creator_name", type_="unique")
        batch.create_unique_constraint("uq_crm_pipelines_name", ["name"])
    op.create_index(
        "uq_crm_pipelines_default_active",
        "crm_pipelines",
        ["is_default"],
        unique=True,
        postgresql_where=sa.text("is_default AND is_active"),
        sqlite_where=sa.text("is_default = 1 AND is_active = 1"),
    )
