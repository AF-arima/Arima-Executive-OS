"""Add normalized, non-price provider verification state.

Revision ID: 20260813_0014_market
Revises: 20260813_0013_voice
Create Date: 2026-08-13
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260813_0014_market"
down_revision: str | None = "20260813_0013_voice"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "market_provider_verifications",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("source", sa.String(length=50), nullable=False),
        sa.Column("canonical", sa.String(length=20), nullable=True),
        sa.Column("state", sa.String(length=50), nullable=False),
        sa.Column("configured", sa.Boolean(), nullable=False),
        sa.Column("authenticated", sa.Boolean(), nullable=False),
        sa.Column("provider_verified", sa.Boolean(), nullable=False),
        sa.Column("account_plan_verified", sa.Boolean(), nullable=False),
        sa.Column("symbol_verified", sa.Boolean(), nullable=False),
        sa.Column("source_verified", sa.Boolean(), nullable=False),
        sa.Column("real_time_verified", sa.Boolean(), nullable=False),
        sa.Column("freshness", sa.String(length=20), nullable=False),
        sa.Column("provider_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(length=100), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_market_provider_verifications")),
    )
    op.create_index(
        "ix_market_provider_verifications_run_canonical",
        "market_provider_verifications",
        ["run_id", "canonical"],
        unique=False,
    )
    op.create_index(
        "ix_market_provider_verifications_provider_checked",
        "market_provider_verifications",
        ["provider", "checked_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_table("market_provider_verifications")
