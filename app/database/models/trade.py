from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, Text, Uuid, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class SettledTradeSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class SettledTradeStatus(str, Enum):
    RECORDED = "recorded"
    REVERSED = "reversed"


class SettledTrade(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "settled_trades"
    __table_args__ = (
        UniqueConstraint("workspace_id", "idempotency_key", name="uq_settled_trade_workspace_idempotency"),
        Index("ix_settled_trades_workspace_user_created", "workspace_id", "target_user_id", "created_at"),
        Index("ix_settled_trades_reversal_of", "reversal_of_id"),
    )

    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    target_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    founder_actor_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    reversal_of_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("settled_trades.id", ondelete="RESTRICT"), nullable=True)
    side: Mapped[str] = mapped_column(String(8), nullable=False)
    base_asset: Mapped[str] = mapped_column(String(32), nullable=False)
    quote_asset: Mapped[str] = mapped_column(String(32), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    price: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    quote_value: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    fee_asset: Mapped[str] = mapped_column(String(32), nullable=False)
    fee_amount: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=SettledTradeStatus.RECORDED.value)
    external_execution_id: Mapped[str | None] = mapped_column(String(180), nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(180), nullable=False)
    payload_fingerprint: Mapped[str] = mapped_column(String(128), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    position_before_quantity: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    position_before_average_cost: Mapped[Decimal | None] = mapped_column(Numeric(38, 18), nullable=True)
    position_before_realized_pnl: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    position_after_quantity: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    position_after_average_cost: Mapped[Decimal | None] = mapped_column(Numeric(38, 18), nullable=True)
    position_after_realized_pnl: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
