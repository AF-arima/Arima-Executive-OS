"""Add authoritative ledger and portfolio foundation."""

from alembic import op
import sqlalchemy as sa

revision = "20260820_0019"
down_revision = "20260820_0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "financial_accounts",
        sa.Column("id", sa.Uuid(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False), sa.Column("user_id", sa.Uuid(), nullable=False), sa.Column("asset", sa.String(32), nullable=False), sa.Column("status", sa.String(24), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"), sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "user_id", "asset", name="uq_financial_account_workspace_user_asset"),
    )
    op.create_index("ix_financial_accounts_workspace_id", "financial_accounts", ["workspace_id"])
    op.create_index("ix_financial_accounts_user_id", "financial_accounts", ["user_id"])
    op.create_table(
        "portfolios",
        sa.Column("id", sa.Uuid(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False), sa.Column("user_id", sa.Uuid(), nullable=False), sa.Column("status", sa.String(24), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"), sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "user_id", name="uq_portfolio_workspace_user"),
    )
    op.create_index("ix_portfolios_workspace_id", "portfolios", ["workspace_id"])
    op.create_index("ix_portfolios_user_id", "portfolios", ["user_id"])
    op.create_table(
        "portfolio_positions",
        sa.Column("id", sa.Uuid(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("portfolio_id", sa.Uuid(), nullable=False), sa.Column("asset", sa.String(32), nullable=False), sa.Column("quantity", sa.Numeric(38, 18), nullable=False), sa.Column("average_cost", sa.Numeric(38, 18), nullable=True), sa.Column("realized_pnl", sa.Numeric(38, 18), nullable=False), sa.Column("unrealized_pnl", sa.Numeric(38, 18), nullable=False),
        sa.ForeignKeyConstraint(["portfolio_id"], ["portfolios.id"], ondelete="CASCADE"), sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("portfolio_id", "asset", name="uq_portfolio_position_asset"),
    )
    op.create_index("ix_portfolio_positions_portfolio_id", "portfolio_positions", ["portfolio_id"])
    op.create_table(
        "financial_transactions",
        sa.Column("id", sa.Uuid(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False), sa.Column("user_id", sa.Uuid(), nullable=True), sa.Column("reference", sa.String(160), nullable=True), sa.Column("transaction_type", sa.String(64), nullable=False), sa.Column("status", sa.String(24), nullable=False), sa.Column("idempotency_key", sa.String(180), nullable=False), sa.Column("actor_id", sa.Uuid(), nullable=True), sa.Column("source", sa.String(80), nullable=False), sa.Column("provenance", sa.JSON(), nullable=False), sa.Column("posted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('pending', 'posted', 'failed', 'reversed', 'cancelled')", name="ck_financial_transaction_status"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"), sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"), sa.ForeignKeyConstraint(["actor_id"], ["users.id"], ondelete="SET NULL"), sa.PrimaryKeyConstraint("id"), sa.UniqueConstraint("workspace_id", "idempotency_key", name="uq_financial_transaction_workspace_idempotency"),
    )
    op.create_index("ix_financial_transactions_workspace_id", "financial_transactions", ["workspace_id"])
    op.create_index("ix_financial_transactions_user_id", "financial_transactions", ["user_id"])
    op.create_index("ix_financial_transactions_workspace_created", "financial_transactions", ["workspace_id", "created_at"])
    op.create_table(
        "ledger_entries",
        sa.Column("id", sa.Uuid(), nullable=False), sa.Column("transaction_id", sa.Uuid(), nullable=False), sa.Column("financial_account_id", sa.Uuid(), nullable=False), sa.Column("asset", sa.String(32), nullable=False), sa.Column("direction", sa.String(8), nullable=False), sa.Column("bucket", sa.String(16), nullable=False), sa.Column("amount", sa.Numeric(38, 18), nullable=False), sa.Column("memo", sa.Text(), nullable=True),
        sa.CheckConstraint("amount > 0", name="ck_ledger_entries_amount_positive"), sa.CheckConstraint("direction IN ('debit', 'credit')", name="ck_ledger_entries_direction"), sa.CheckConstraint("bucket IN ('available', 'reserved', 'pending')", name="ck_ledger_entries_bucket"), sa.ForeignKeyConstraint(["transaction_id"], ["financial_transactions.id"], ondelete="RESTRICT"), sa.ForeignKeyConstraint(["financial_account_id"], ["financial_accounts.id"], ondelete="RESTRICT"), sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ledger_entries_transaction_id", "ledger_entries", ["transaction_id"])
    op.create_index("ix_ledger_entries_financial_account_id", "ledger_entries", ["financial_account_id"])
    op.create_index("ix_ledger_entries_account_bucket", "ledger_entries", ["financial_account_id", "bucket"])


def downgrade() -> None:
    op.drop_index("ix_ledger_entries_account_bucket", table_name="ledger_entries")
    op.drop_index("ix_ledger_entries_financial_account_id", table_name="ledger_entries")
    op.drop_index("ix_ledger_entries_transaction_id", table_name="ledger_entries")
    op.drop_table("ledger_entries")
    op.drop_index("ix_financial_transactions_workspace_created", table_name="financial_transactions")
    op.drop_index("ix_financial_transactions_user_id", table_name="financial_transactions")
    op.drop_index("ix_financial_transactions_workspace_id", table_name="financial_transactions")
    op.drop_table("financial_transactions")
    op.drop_index("ix_portfolio_positions_portfolio_id", table_name="portfolio_positions")
    op.drop_table("portfolio_positions")
    op.drop_index("ix_portfolios_user_id", table_name="portfolios")
    op.drop_index("ix_portfolios_workspace_id", table_name="portfolios")
    op.drop_table("portfolios")
    op.drop_index("ix_financial_accounts_user_id", table_name="financial_accounts")
    op.drop_index("ix_financial_accounts_workspace_id", table_name="financial_accounts")
    op.drop_table("financial_accounts")
