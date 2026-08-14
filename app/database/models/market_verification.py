from datetime import datetime
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models.base import Base, UUIDPrimaryKeyMixin


class MarketProviderVerification(UUIDPrimaryKeyMixin, Base):
    """Append-only normalized provider verification evidence.

    The table intentionally has no credential, raw response, price, quote,
    candle, or other market-value column.
    """

    __tablename__ = "market_provider_verifications"

    run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    canonical: Mapped[str | None] = mapped_column(String(20), nullable=True)
    state: Mapped[str] = mapped_column(String(50), nullable=False)
    configured: Mapped[bool] = mapped_column(Boolean, nullable=False)
    authenticated: Mapped[bool] = mapped_column(Boolean, nullable=False)
    provider_verified: Mapped[bool] = mapped_column(Boolean, nullable=False)
    account_plan_verified: Mapped[bool] = mapped_column(Boolean, nullable=False)
    symbol_verified: Mapped[bool] = mapped_column(Boolean, nullable=False)
    source_verified: Mapped[bool] = mapped_column(Boolean, nullable=False)
    real_time_verified: Mapped[bool] = mapped_column(Boolean, nullable=False)
    freshness: Mapped[str] = mapped_column(String(20), nullable=False)
    provider_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    reason: Mapped[str | None] = mapped_column(String(100), nullable=True)

    __table_args__ = (
        Index(
            "ix_market_provider_verifications_run_canonical",
            "run_id",
            "canonical",
        ),
        Index(
            "ix_market_provider_verifications_provider_checked",
            "provider",
            "checked_at",
        ),
    )
