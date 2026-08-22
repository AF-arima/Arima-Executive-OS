"""Add signal and safe dry-run metadata to execution decisions."""

from alembic import op
import sqlalchemy as sa


revision = "20260820_0021"
down_revision = "20260820_0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("trade_executions", sa.Column("signal_id", sa.Uuid(), nullable=True))
    op.add_column("trade_executions", sa.Column("provider", sa.String(50), nullable=True))
    op.add_column("trade_executions", sa.Column("dry_run_result", sa.JSON(), nullable=True))
    op.execute("UPDATE trade_executions SET signal_id = id, provider = 'unknown', dry_run_result = '{}' WHERE signal_id IS NULL")
    with op.batch_alter_table("trade_executions", recreate="always") as batch_op:
        batch_op.alter_column("signal_id", nullable=False)
        batch_op.alter_column("provider", nullable=False)
        batch_op.alter_column("dry_run_result", nullable=False)
    op.create_index("ix_trade_executions_signal_id", "trade_executions", ["signal_id"])


def downgrade() -> None:
    op.drop_index("ix_trade_executions_signal_id", table_name="trade_executions")
    op.drop_column("trade_executions", "dry_run_result")
    op.drop_column("trade_executions", "provider")
    op.drop_column("trade_executions", "signal_id")
