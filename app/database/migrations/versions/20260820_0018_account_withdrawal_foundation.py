"""Add account support and withdrawal request foundation."""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260820_0018"
down_revision: str | None = "20260820_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("audit_logs", sa.Column("event_type", sa.String(100), nullable=True))
    op.add_column("audit_logs", sa.Column("event_metadata", sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
    op.create_index("ix_audit_logs_event_type", "audit_logs", ["event_type"])
    with op.batch_alter_table("audit_logs") as batch:
        batch.drop_constraint("audit_entity", type_="check")
        batch.create_check_constraint(
            "audit_entity",
            "entity IN ('project', 'task', 'company', 'contact', 'lead', 'pipeline', 'pipeline_stage', 'deal', 'crm_note', 'crm_activity', 'mailbox', 'email_template', 'email_draft', 'sequence', 'campaign', 'automation', 'data_feed_observation', 'voice_authorization_diagnostic', 'account', 'withdrawal', 'withdrawal_circuit_breaker')",
        )
    op.create_table(
        "withdrawal_circuit_breakers",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("reason", sa.String(500), nullable=True),
        sa.Column("changed_by_id", sa.Uuid(), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["changed_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id"),
    )
    op.create_table(
        "withdrawal_requests",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("amount", sa.Numeric(38, 18), nullable=False),
        sa.Column("currency", sa.String(12), nullable=False),
        sa.Column("destination_wallet_address", sa.String(128), nullable=False),
        sa.Column("network", sa.String(64), nullable=False),
        sa.Column("risk_acknowledged", sa.Boolean(), nullable=False),
        sa.Column("idempotency_key", sa.String(160), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("state_reason", sa.Text(), nullable=True),
        sa.Column("reviewed_by_id", sa.Uuid(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by_id", sa.Uuid(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notification_status", sa.String(32), nullable=False),
        sa.Column("notification_error", sa.String(160), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reviewed_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["approved_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index("ix_withdrawal_requests_workspace_state_created", "withdrawal_requests", ["workspace_id", "state", "created_at"])
    op.create_index("ix_withdrawal_requests_user_created", "withdrawal_requests", ["user_id", "created_at"])
    op.create_index("ix_withdrawal_requests_workspace_id", "withdrawal_requests", ["workspace_id"])
    op.create_index("ix_withdrawal_requests_user_id", "withdrawal_requests", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_withdrawal_requests_user_id", table_name="withdrawal_requests")
    op.drop_index("ix_withdrawal_requests_workspace_id", table_name="withdrawal_requests")
    op.execute(sa.text("DELETE FROM audit_logs WHERE entity IN ('account', 'withdrawal', 'withdrawal_circuit_breaker')"))
    with op.batch_alter_table("audit_logs") as batch:
        batch.drop_constraint("audit_entity", type_="check")
        batch.create_check_constraint(
            "audit_entity",
            "entity IN ('project', 'task', 'company', 'contact', 'lead', 'pipeline', 'pipeline_stage', 'deal', 'crm_note', 'crm_activity', 'mailbox', 'email_template', 'email_draft', 'sequence', 'campaign', 'automation', 'data_feed_observation', 'voice_authorization_diagnostic')",
        )
    op.drop_index("ix_withdrawal_requests_user_created", table_name="withdrawal_requests")
    op.drop_index("ix_withdrawal_requests_workspace_state_created", table_name="withdrawal_requests")
    op.drop_table("withdrawal_requests")
    op.drop_table("withdrawal_circuit_breakers")
    op.drop_index("ix_audit_logs_event_type", table_name="audit_logs")
    op.drop_column("audit_logs", "event_metadata")
    op.drop_column("audit_logs", "event_type")
    op.drop_column("users", "password_changed_at")
