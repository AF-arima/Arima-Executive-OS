from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.database.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class DataFeedObservation(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Immutable, founder-entered provenance for an operational data feed.

    Observations intentionally contain no business metric payload. They record
    only source, freshness and authoring provenance until a verified ingestion
    contract exists for the respective feed.
    """

    __tablename__ = "data_feed_observations"

    feed_key: Mapped[str] = mapped_column(String(100), nullable=False)
    source: Mapped[str] = mapped_column(String(500), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    entered_by_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    correlation_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        index=True,
        nullable=False,
    )

    __table_args__ = (
        Index(
            "ix_data_feed_observations_feed_observed",
            "feed_key",
            "observed_at",
        ),
        Index(
            "ix_data_feed_observations_entered_created",
            "entered_by_id",
            "created_at",
        ),
    )
