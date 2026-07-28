from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.database.models.user import User


class Workspace(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """A tenant boundary. Every new account receives one personal workspace."""

    __tablename__ = "workspaces"

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    owner_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )

    __table_args__ = (Index("ix_workspaces_owner_id", "owner_id"),)

    owner: Mapped[User] = relationship(
        back_populates="owned_workspace",
        foreign_keys=[owner_id],
    )
    memberships: Mapped[list[WorkspaceMembership]] = relationship(
        back_populates="workspace",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class WorkspaceMembership(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """Future-safe workspace membership; personal workspaces begin with owner."""

    __tablename__ = "workspace_memberships"

    workspace_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(50), default="owner", nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "user_id",
            name="uq_workspace_memberships_workspace_user",
        ),
    )

    workspace: Mapped[Workspace] = relationship(back_populates="memberships")
    user: Mapped[User] = relationship(back_populates="workspace_memberships")
