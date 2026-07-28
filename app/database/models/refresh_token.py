from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import (
    Base,
    UUIDPrimaryKeyMixin,
    utc_now,
)

if TYPE_CHECKING:
    from app.database.models.user import User


class RefreshTokenSession(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "refresh_token_sessions"
    __table_args__ = (
        Index(
            "ix_refresh_token_sessions_user_family_active",
            "user_id",
            "family_id",
            "revoked_at",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    token_jti: Mapped[str] = mapped_column(
        String(36),
        unique=True,
        index=True,
        nullable=False,
    )
    family_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        index=True,
        nullable=False,
    )
    parent_jti: Mapped[str | None] = mapped_column(String(36), nullable=True)
    is_persistent: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    user_agent: Mapped[str | None] = mapped_column(String(512), nullable=True)
    ip_address: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    revoked_reason: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    user: Mapped[User] = relationship(
        back_populates="refresh_token_sessions",
    )
