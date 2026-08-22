from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, utc_now

if TYPE_CHECKING:
    from app.database.models.user import User
    from app.database.models.workspace import Workspace


class WithdrawalState(str, Enum):
    REQUESTED = "requested"
    UNDER_REVIEW = "under_review"
    APPROVED = "approved"
    EXECUTING = "executing"
    COMPLETED = "completed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    FAILED = "failed"
    BLOCKED = "blocked"


class WithdrawalCircuitState(str, Enum):
    ENABLED = "enabled"
    PAUSED = "paused"
    EMERGENCY_STOP = "emergency_stop"


class WithdrawalRequest(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "withdrawal_requests"
    __table_args__ = (
        Index("ix_withdrawal_requests_workspace_state_created", "workspace_id", "state", "created_at"),
        Index("ix_withdrawal_requests_user_created", "user_id", "created_at"),
    )

    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), index=True, nullable=False)
    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(38, 18), nullable=False)
    currency: Mapped[str] = mapped_column(String(12), nullable=False)
    destination_wallet_address: Mapped[str] = mapped_column(String(128), nullable=False)
    network: Mapped[str] = mapped_column(String(64), nullable=False)
    risk_acknowledged: Mapped[bool] = mapped_column(nullable=False, default=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(128), index=True, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default=WithdrawalState.REQUESTED.value)
    state_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_by_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    notification_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    notification_error: Mapped[str | None] = mapped_column(String(160), nullable=True)

    user: Mapped[User] = relationship(foreign_keys=[user_id])
    workspace: Mapped[Workspace] = relationship()


class WithdrawalCircuitBreaker(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "withdrawal_circuit_breakers"

    workspace_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), unique=True, nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False, default=WithdrawalCircuitState.ENABLED.value)
    reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    changed_by_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, nullable=False)
