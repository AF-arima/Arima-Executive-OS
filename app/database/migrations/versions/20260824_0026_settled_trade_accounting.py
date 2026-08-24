"""Add settled manual trade accounting and clearing accounts."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260824_0026"
down_revision: str | None = "20260824_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_AUDIT_ENTITIES = (
    "project", "task", "company", "contact", "lead", "pipeline", "pipeline_stage",
    "deal", "crm_note", "crm_activity", "mailbox", "email_template", "email_draft",
    "sequence", "campaign", "automation", "data_feed_observation",
    "voice_authorization_diagnostic", "account", "withdrawal", "withdrawal_circuit_breaker",
    "document", "trade",
)


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        # PostgreSQL cannot recreate this table: ledger_entries has a foreign
        # key that depends on financial_accounts' primary-key index.
        op.alter_column("financial_accounts", "user_id", existing_type=sa.Uuid(), nullable=True)
        op.add_column(
            "financial_accounts",
            sa.Column("account_kind", sa.String(16), nullable=False, server_default="customer"),
        )
        op.create_check_constraint(
            "ck_financial_account_kind",
            "financial_accounts",
            "account_kind IN ('customer', 'clearing')",
        )
        op.create_check_constraint(
            "ck_financial_account_owner",
            "financial_accounts",
            "(account_kind = 'customer' AND user_id IS NOT NULL) OR (account_kind = 'clearing' AND user_id IS NULL)",
        )
    else:
        with op.batch_alter_table("financial_accounts", recreate="always") as batch:
            batch.alter_column("user_id", existing_type=sa.Uuid(), nullable=True)
            batch.add_column(sa.Column("account_kind", sa.String(16), nullable=False, server_default="customer"))
            batch.create_check_constraint("ck_financial_account_kind", "account_kind IN ('customer', 'clearing')")
            batch.create_check_constraint("ck_financial_account_owner", "(account_kind = 'customer' AND user_id IS NOT NULL) OR (account_kind = 'clearing' AND user_id IS NULL)")
    op.create_index("uq_financial_account_workspace_clearing_asset", "financial_accounts", ["workspace_id", "asset"], unique=True, sqlite_where=sa.text("account_kind = 'clearing'"), postgresql_where=sa.text("account_kind = 'clearing'"))

    op.create_table(
        "settled_trades",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("target_user_id", sa.Uuid(), nullable=False),
        sa.Column("founder_actor_id", sa.Uuid(), nullable=False),
        sa.Column("reversal_of_id", sa.Uuid(), nullable=True),
        sa.Column("side", sa.String(8), nullable=False),
        sa.Column("base_asset", sa.String(32), nullable=False),
        sa.Column("quote_asset", sa.String(32), nullable=False),
        sa.Column("quantity", sa.Numeric(38, 18), nullable=False),
        sa.Column("price", sa.Numeric(38, 18), nullable=False),
        sa.Column("quote_value", sa.Numeric(38, 18), nullable=False),
        sa.Column("fee_asset", sa.String(32), nullable=False),
        sa.Column("fee_amount", sa.Numeric(38, 18), nullable=False),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("external_execution_id", sa.String(180), nullable=True),
        sa.Column("idempotency_key", sa.String(180), nullable=False),
        sa.Column("payload_fingerprint", sa.String(128), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("position_before_quantity", sa.Numeric(38, 18), nullable=False),
        sa.Column("position_before_average_cost", sa.Numeric(38, 18), nullable=True),
        sa.Column("position_before_realized_pnl", sa.Numeric(38, 18), nullable=False),
        sa.Column("position_after_quantity", sa.Numeric(38, 18), nullable=False),
        sa.Column("position_after_average_cost", sa.Numeric(38, 18), nullable=True),
        sa.Column("position_after_realized_pnl", sa.Numeric(38, 18), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspaces.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["target_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["founder_actor_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["reversal_of_id"], ["settled_trades.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "idempotency_key", name="uq_settled_trade_workspace_idempotency"),
    )
    op.create_index("ix_settled_trades_workspace_id", "settled_trades", ["workspace_id"])
    op.create_index("ix_settled_trades_target_user_id", "settled_trades", ["target_user_id"])
    op.create_index("ix_settled_trades_founder_actor_id", "settled_trades", ["founder_actor_id"])
    op.create_index("ix_settled_trades_workspace_user_created", "settled_trades", ["workspace_id", "target_user_id", "created_at"])
    op.create_index("ix_settled_trades_reversal_of", "settled_trades", ["reversal_of_id"])
    with op.batch_alter_table("financial_transactions") as batch:
        batch.add_column(sa.Column("trade_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key("fk_financial_transactions_trade_id_settled_trades", "settled_trades", ["trade_id"], ["id"], ondelete="SET NULL")
        batch.create_index("ix_financial_transactions_trade_id", ["trade_id"])
    with op.batch_alter_table("audit_logs") as batch:
        batch.drop_constraint("audit_entity", type_="check")
        batch.create_check_constraint("audit_entity", "entity IN (" + ", ".join(repr(item) for item in _AUDIT_ENTITIES) + ")")


def downgrade() -> None:
    bind = op.get_bind()
    settled_trade_count = bind.execute(sa.text("SELECT COUNT(*) FROM settled_trades")).scalar_one()
    trade_transaction_count = bind.execute(sa.text("SELECT COUNT(*) FROM financial_transactions WHERE trade_id IS NOT NULL")).scalar_one()
    clearing_account_count = bind.execute(sa.text("SELECT COUNT(*) FROM financial_accounts WHERE user_id IS NULL")).scalar_one()
    if settled_trade_count:
        raise RuntimeError("Downgrade blocked: immutable settled trade history exists; financial history must not be deleted")
    if trade_transaction_count:
        raise RuntimeError("Downgrade blocked: financial transactions reference settled trade history")
    if clearing_account_count:
        raise RuntimeError("Downgrade blocked: clearing accounts with NULL user_id exist; accounting accounts must not be rewritten")
    with op.batch_alter_table("audit_logs") as batch:
        batch.drop_constraint("audit_entity", type_="check")
        batch.create_check_constraint("audit_entity", "entity IN (" + ", ".join(repr(item) for item in _AUDIT_ENTITIES[:-1]) + ")")
    with op.batch_alter_table("financial_transactions") as batch:
        batch.drop_index("ix_financial_transactions_trade_id")
        batch.drop_constraint("fk_financial_transactions_trade_id_settled_trades", type_="foreignkey")
        batch.drop_column("trade_id")
    for index in ("ix_settled_trades_reversal_of", "ix_settled_trades_workspace_user_created", "ix_settled_trades_founder_actor_id", "ix_settled_trades_target_user_id", "ix_settled_trades_workspace_id"):
        op.drop_index(index, table_name="settled_trades")
    op.drop_table("settled_trades")
    op.drop_index("uq_financial_account_workspace_clearing_asset", table_name="financial_accounts")
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint("ck_financial_account_owner", "financial_accounts", type_="check")
        op.drop_constraint("ck_financial_account_kind", "financial_accounts", type_="check")
        op.drop_column("financial_accounts", "account_kind")
        op.alter_column("financial_accounts", "user_id", existing_type=sa.Uuid(), nullable=False)
    else:
        with op.batch_alter_table("financial_accounts", recreate="always") as batch:
            batch.drop_constraint("ck_financial_account_owner", type_="check")
            batch.drop_constraint("ck_financial_account_kind", type_="check")
            batch.drop_column("account_kind")
            batch.alter_column("user_id", existing_type=sa.Uuid(), nullable=False)
