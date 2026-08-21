from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, JSON, Numeric, String, Uuid, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, utc_now


class TradeExecution(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Auditable execution decision; no broker submission occurs here."""

    __tablename__ = "trade_executions"
    __table_args__ = (
        UniqueConstraint("workspace_id", "idempotency_key", name="uq_trade_execution_workspace_idempotency"),
        Index("ix_trade_executions_workspace_state_created", "workspace_id", "state", "created_at"),
    )

    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True)
    actor_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    account_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True)
    signal_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    asset: Mapped[str] = mapped_column(String(32), nullable=False)
    strategy: Mapped[str] = mapped_column(String(100), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(180), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    risk_decision: Mapped[str] = mapped_column(String(160), nullable=False)
    circuit_state: Mapped[str] = mapped_column(String(32), nullable=False)
    execution_permission: Mapped[bool] = mapped_column(nullable=False, default=False)
    rejection_reason: Mapped[str | None] = mapped_column(String(240), nullable=True)
    signal_provenance: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    dry_run_result: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now)
