"""Add auditable, disabled QTrade execution decisions."""

from alembic import op
import sqlalchemy as sa


revision = "20260820_0020"
down_revision = "20260820_0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "trade_executions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("asset", sa.String(32), nullable=False),
        sa.Column("strategy", sa.String(100), nullable=False),
        sa.Column("quantity", sa.Numeric(38, 18), nullable=False),
        sa.Column("idempotency_key", sa.String(180), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("risk_decision", sa.String(160), nullable=False),
        sa.Column("circuit_state", sa.String(32), nullable=False),
        sa.Column("execution_permission", sa.Boolean(), nullable=False),
        sa.Column("rejection_reason", sa.String(240), nullable=True),
        sa.Column("signal_provenance", sa.JSON(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["account_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "idempotency_key", name="uq_trade_execution_workspace_idempotency"),
    )
    op.create_index("ix_trade_executions_tenant_id", "trade_executions", ["tenant_id"])
    op.create_index("ix_trade_executions_workspace_id", "trade_executions", ["workspace_id"])
    op.create_index("ix_trade_executions_actor_id", "trade_executions", ["actor_id"])
    op.create_index("ix_trade_executions_account_id", "trade_executions", ["account_id"])
    op.create_index("ix_trade_executions_workspace_state_created", "trade_executions", ["workspace_id", "state", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_trade_executions_workspace_state_created", table_name="trade_executions")
    op.drop_index("ix_trade_executions_account_id", table_name="trade_executions")
    op.drop_index("ix_trade_executions_actor_id", table_name="trade_executions")
    op.drop_index("ix_trade_executions_workspace_id", table_name="trade_executions")
    op.drop_index("ix_trade_executions_tenant_id", table_name="trade_executions")
    op.drop_table("trade_executions")
