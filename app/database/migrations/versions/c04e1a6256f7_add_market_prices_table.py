"""add market prices table

Revision ID: c04e1a6256f7
Revises: 20260728_0011
Create Date: 2026-08-06 12:35:01.404861

This exact revision identifier is the missing parent referenced by the
committed ``c0b73382a5a4`` head. The original generated copy was present only
in an uncommitted worktree and contained no schema operations. This restored
revision supplies the model-matching, idempotent operation without reviving the
unsafe uncommitted migration that duplicated ``user_roles`` constraints.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c04e1a6256f7"
down_revision: Union[str, Sequence[str], None] = "20260728_0011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _market_prices() -> sa.Table:
    metadata = sa.MetaData()
    table = sa.Table(
        "market_prices",
        metadata,
        sa.Column("symbol", sa.String(length=50), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("market_timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_market_prices")),
    )
    sa.Index(op.f("ix_market_prices_symbol"), table.c.symbol)
    return table


def upgrade() -> None:
    """Create the table when an earlier manually-applied deployment lacks it."""
    _market_prices().create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    """Remove the table while downgrading this revision."""
    _market_prices().drop(bind=op.get_bind(), checkfirst=True)
