from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, JSON, Numeric, String, Text, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class FinancialTransactionStatus(str, Enum):
    PENDING = "pending"
    POSTED = "posted"
    FAILED = "failed"
    REVERSED = "reversed"
    CANCELLED = "cancelled"


class LedgerDirection(str, Enum):
    DEBIT = "debit"
    CREDIT = "credit"


class LedgerBucket(str, Enum):
    AVAILABLE = "available"
    RESERVED = "reserved"
    PENDING = "pending"


class PortfolioStatus(str, Enum):
    ACTIVE = "active"
    RESTRICTED = "restricted"
    CLOSED = "closed"


class FinancialAccount(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "financial_accounts"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id", "asset", name="uq_financial_account_workspace_user_asset"),)

    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True, nullable=False)
    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    asset: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="active")


class Portfolio(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "portfolios"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id", name="uq_portfolio_workspace_user"),)

    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True, nullable=False)
    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default=PortfolioStatus.ACTIVE.value)


class PortfolioPosition(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "portfolio_positions"
    __table_args__ = (UniqueConstraint("portfolio_id", "asset", name="uq_portfolio_position_asset"),)

    portfolio_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("portfolios.id", ondelete="CASCADE"), index=True, nullable=False)
    asset: Mapped[str] = mapped_column(String(32), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False, default=Decimal("0"))
    average_cost: Mapped[Decimal | None] = mapped_column(Numeric(38, 18), nullable=True)
    realized_pnl: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False, default=Decimal("0"))
    unrealized_pnl: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False, default=Decimal("0"))


class FinancialTransaction(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "financial_transactions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "idempotency_key", name="uq_financial_transaction_workspace_idempotency"),
        Index("ix_financial_transactions_workspace_created", "workspace_id", "created_at"),
        CheckConstraint("status IN ('pending', 'posted', 'failed', 'reversed', 'cancelled')", name="ck_financial_transaction_status"),
    )

    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True, nullable=False)
    user_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), index=True, nullable=True)
    reference: Mapped[str | None] = mapped_column(String(160), nullable=True)
    transaction_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default=FinancialTransactionStatus.PENDING.value)
    idempotency_key: Mapped[str] = mapped_column(String(180), nullable=False)
    actor_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    source: Mapped[str] = mapped_column(String(80), nullable=False)
    provenance: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LedgerEntry(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "ledger_entries"
    __table_args__ = (
        CheckConstraint("amount > 0", name="ck_ledger_entries_amount_positive"),
        CheckConstraint("direction IN ('debit', 'credit')", name="ck_ledger_entries_direction"),
        CheckConstraint("bucket IN ('available', 'reserved', 'pending')", name="ck_ledger_entries_bucket"),
        Index("ix_ledger_entries_account_bucket", "financial_account_id", "bucket"),
    )

    transaction_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("financial_transactions.id", ondelete="RESTRICT"), index=True, nullable=False)
    financial_account_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("financial_accounts.id", ondelete="RESTRICT"), index=True, nullable=False)
    asset: Mapped[str] = mapped_column(String(32), nullable=False)
    direction: Mapped[str] = mapped_column(String(8), nullable=False)
    bucket: Mapped[str] = mapped_column(String(16), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    memo: Mapped[str | None] = mapped_column(Text, nullable=True)
