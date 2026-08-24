"""Add tenant-scoped customer document metadata."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260824_0025"
down_revision: str | None = "20260820_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_AUDIT_ENTITIES = (
    "project", "task", "company", "contact", "lead", "pipeline",
    "pipeline_stage", "deal", "crm_note", "crm_activity", "mailbox",
    "email_template", "email_draft", "sequence", "campaign", "automation",
    "data_feed_observation", "voice_authorization_diagnostic", "account",
    "withdrawal", "withdrawal_circuit_breaker", "document",
)


def upgrade() -> None:
    op.create_table(
        "customer_documents",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("target_user_id", sa.Uuid(), nullable=False),
        sa.Column("uploaded_by_id", sa.Uuid(), nullable=False),
        sa.Column("storage_key", sa.String(500), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("content_type", sa.String(200), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("checksum_sha256", sa.String(64), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("description", sa.String(2000), nullable=True),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["uploaded_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_index("ix_customer_documents_workspace_id", "customer_documents", ["workspace_id"])
    op.create_index("ix_customer_documents_target_user_id", "customer_documents", ["target_user_id"])
    op.create_index("ix_customer_documents_uploaded_by_id", "customer_documents", ["uploaded_by_id"])
    op.create_index("ix_customer_documents_workspace_user_status", "customer_documents", ["workspace_id", "target_user_id", "status"])
    with op.batch_alter_table("audit_logs") as batch:
        batch.drop_constraint("audit_entity", type_="check")
        batch.create_check_constraint("audit_entity", "entity IN (" + ", ".join(repr(item) for item in _AUDIT_ENTITIES) + ")")


def downgrade() -> None:
    with op.batch_alter_table("audit_logs") as batch:
        batch.drop_constraint("audit_entity", type_="check")
        batch.create_check_constraint("audit_entity", "entity IN (" + ", ".join(repr(item) for item in _AUDIT_ENTITIES[:-1]) + ")")
    op.drop_index("ix_customer_documents_workspace_user_status", table_name="customer_documents")
    op.drop_index("ix_customer_documents_uploaded_by_id", table_name="customer_documents")
    op.drop_index("ix_customer_documents_target_user_id", table_name="customer_documents")
    op.drop_index("ix_customer_documents_workspace_id", table_name="customer_documents")
    op.drop_table("customer_documents")
