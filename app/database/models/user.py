from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.database.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

if TYPE_CHECKING:
    from app.database.models.audit_log import AuditLog
    from app.database.models.notification import Notification
    from app.database.models.project import Project
    from app.database.models.refresh_token import RefreshTokenSession
    from app.database.models.role import Role
    from app.database.models.task import Task
    from app.database.models.user_role import UserRole
    from app.database.models.workspace import Workspace, WorkspaceMembership
    from app.database.models.security import SecurityEvent, SecurityToken


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(
        String(320),
        unique=True,
        index=True,
        nullable=False,
    )
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    first_name: Mapped[str] = mapped_column(String(100), nullable=False)
    last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )
    is_verified: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    failed_login_attempts: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        index=True,
        nullable=True,
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_login_ip: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )

    __table_args__ = (
        Index("ux_users_email_lower", func.lower(email), unique=True),
    )

    @validates("email")
    def normalize_email(self, _: str, value: str) -> str:
        return value.strip().lower()

    user_roles: Mapped[list[UserRole]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
        overlaps="roles,users",
    )
    roles: Mapped[list[Role]] = relationship(
        secondary="user_roles",
        back_populates="users",
        lazy="selectin",
        overlaps="role,user,user_roles",
    )
    owned_projects: Mapped[list[Project]] = relationship(
        back_populates="owner",
        foreign_keys="Project.owner_id",
        passive_deletes="all",
    )
    created_projects: Mapped[list[Project]] = relationship(
        back_populates="creator",
        foreign_keys="Project.created_by",
        passive_deletes="all",
    )
    assigned_tasks: Mapped[list[Task]] = relationship(
        back_populates="assignee",
        foreign_keys="Task.assignee_id",
        passive_deletes=True,
    )
    created_tasks: Mapped[list[Task]] = relationship(
        back_populates="creator",
        foreign_keys="Task.created_by",
        passive_deletes="all",
    )
    audit_logs: Mapped[list[AuditLog]] = relationship(
        back_populates="actor",
        foreign_keys="AuditLog.actor_id",
        passive_deletes=True,
    )
    notifications: Mapped[list[Notification]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    refresh_token_sessions: Mapped[list[RefreshTokenSession]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    owned_workspace: Mapped[Workspace | None] = relationship(
        back_populates="owner",
        foreign_keys="Workspace.owner_id",
        uselist=False,
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    workspace_memberships: Mapped[list[WorkspaceMembership]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    security_tokens: Mapped[list[SecurityToken]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    security_events: Mapped[list[SecurityEvent]] = relationship(
        back_populates="user",
        passive_deletes=True,
    )
