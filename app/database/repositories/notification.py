from datetime import datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import func, or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Notification, NotificationType, Project, Task
from app.database.repositories.base import AsyncRepository
from app.database.repositories.pagination import Page


class NotificationRepository(AsyncRepository[Notification]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(Notification, session)

    async def list_for_user(
        self,
        user_id: UUID,
        *,
        now: datetime,
        is_read: bool | None,
        notification_type: NotificationType | None,
        limit: int,
        offset: int,
    ) -> Page[Notification]:
        filters = [
            Notification.user_id == user_id,
            or_(
                Notification.expires_at.is_(None),
                Notification.expires_at > now,
            ),
        ]
        if is_read is not None:
            filters.append(Notification.is_read.is_(is_read))
        if notification_type is not None:
            filters.append(Notification.type == notification_type)
        total = await self.session.scalar(
            select(func.count(Notification.id)).where(*filters)
        )
        rows = await self.session.scalars(
            select(Notification)
            .where(*filters)
            .order_by(
                Notification.created_at.desc(),
                Notification.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
        return Page(
            items=list(rows.all()),
            total=int(total or 0),
            limit=limit,
            offset=offset,
        )

    async def unread_count(
        self,
        user_id: UUID,
        *,
        now: datetime,
    ) -> int:
        value = await self.session.scalar(
            select(func.count(Notification.id)).where(
                Notification.user_id == user_id,
                Notification.is_read.is_(False),
                or_(
                    Notification.expires_at.is_(None),
                    Notification.expires_at > now,
                ),
            )
        )
        return int(value or 0)

    async def get_owned_for_update(
        self,
        notification_id: UUID,
        user_id: UUID,
    ) -> Notification | None:
        return await self.session.scalar(
            select(Notification)
            .where(
                Notification.id == notification_id,
                Notification.user_id == user_id,
            )
            .with_for_update()
        )

    async def mark_all_read(
        self,
        user_id: UUID,
        *,
        now: datetime,
    ) -> int:
        result = cast(
            CursorResult[Any],
            await self.session.execute(
                update(Notification)
                .where(
                    Notification.user_id == user_id,
                    Notification.is_read.is_(False),
                    or_(
                        Notification.expires_at.is_(None),
                        Notification.expires_at > now,
                    ),
                )
                .values(is_read=True, read_at=now)
            ),
        )
        return int(result.rowcount or 0)

    async def due_notification_candidates(
        self,
        *,
        now: datetime,
        due_before: datetime,
    ) -> list[Task]:
        rows = await self.session.scalars(
            select(Task)
            .join(Project, Project.id == Task.project_id)
            .where(
                Task.assignee_id.is_not(None),
                Task.completed_at.is_(None),
                Task.due_date.is_not(None),
                Task.due_date <= due_before,
                Project.archived_at.is_(None),
            )
            .order_by(Task.id)
        )
        return list(rows.all())

    async def dedupe_key_exists(self, dedupe_key: str) -> bool:
        value = await self.session.scalar(
            select(Notification.id).where(
                Notification.dedupe_key == dedupe_key
            )
        )
        return value is not None
