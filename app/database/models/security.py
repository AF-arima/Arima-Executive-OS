from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
    utc_now,
)

if TYPE_CHECKING:
    from app.database.models.user import User


class SecurityTokenPurpose(str, Enum):
    EMAIL_VERIFICATION = "email_verification"
    PASSWORD_RESET = "password_reset"
    EMAIL_CHANGE = "email_change"


class SecurityToken(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A single-use, hashed security token. Raw values never reach storage."""

    __tablename__ = "security_tokens"

    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    purpose: Mapped[SecurityTokenPurpose] = mapped_column(
        SqlEnum(
            SecurityTokenPurpose,
            native_enum=False,
            create_constraint=True,
        ),
        index=True,
        nullable=False,
    )
    token_hash: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        index=True,
        nullable=False,
    )
    target_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        index=True,
        nullable=False,
    )
    consumed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    invalidated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    user: Mapped[User] = relationship(back_populates="security_tokens")


class SecurityEvent(UUIDPrimaryKeyMixin, Base):
    """Append-only audit trail for account and session security events."""

    __tablename__ = "security_events"

    user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    event_type: Mapped[str] = mapped_column(String(100), index=True, nullable=False)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    event_metadata: Mapped[dict[str, object]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        index=True,
        nullable=False,
    )

    user: Mapped[User | None] = relationship(back_populates="security_events")


class RateLimitBucket(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Database-backed fixed-window counter for security-sensitive routes."""

    __tablename__ = "rate_limit_buckets"
    __table_args__ = (
        UniqueConstraint(
            "scope",
            "key",
            "window_started_at",
            name="uq_rate_limit_buckets_scope_key_window",
        ),
        Index(
            "ix_rate_limit_buckets_scope_key_window",
            "scope",
            "key",
            "window_started_at",
        ),
    )

    scope: Mapped[str] = mapped_column(String(100), nullable=False)
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    window_started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
