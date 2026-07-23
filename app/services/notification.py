from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    Notification,
    NotificationType,
    Project,
    ProjectStatus,
    Task,
    User,
)
from app.database.repositories import NotificationRepository
from app.schemas.notification import (
    NotificationList,
    NotificationResponse,
    ReadAllResponse,
    UnreadCountResponse,
)
from app.services.exceptions import ResourceNotFoundError

UTC = timezone.utc


def enqueue_task_assignment(
    session: AsyncSession,
    *,
    task: Task,
    user_id: UUID,
) -> None:
    session.add(
        Notification(
            user_id=user_id,
            type=NotificationType.TASK_ASSIGNED,
            title="Task assigned",
            message="A task was assigned to you.",
            entity_type="task",
            entity_id=task.id,
        )
    )


def enqueue_project_status_change(
    session: AsyncSession,
    *,
    project: Project,
    status: ProjectStatus,
) -> None:
    session.add(
        Notification(
            user_id=project.owner_id,
            type=NotificationType.PROJECT_STATUS_CHANGED,
            title="Project status changed",
            message=f"Project status changed to {status.value}.",
            entity_type="project",
            entity_id=project.id,
        )
    )


class NotificationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = NotificationRepository(session)

    async def list(
        self,
        actor: User,
        *,
        is_read: bool | None,
        notification_type: NotificationType | None,
        limit: int,
        offset: int,
    ) -> NotificationList:
        page = await self.repository.list_for_user(
            actor.id,
            now=datetime.now(UTC),
            is_read=is_read,
            notification_type=notification_type,
            limit=limit,
            offset=offset,
        )
        return NotificationList(
            items=[
                NotificationResponse.model_validate(item)
                for item in page.items
            ],
            total=page.total,
            limit=page.limit,
            offset=page.offset,
        )

    async def unread_count(self, actor: User) -> UnreadCountResponse:
        count = await self.repository.unread_count(
            actor.id,
            now=datetime.now(UTC),
        )
        return UnreadCountResponse(unread_count=count)

    async def mark_read(
        self,
        notification_id: UUID,
        actor: User,
    ) -> NotificationResponse:
        notification = await self.repository.get_owned_for_update(
            notification_id,
            actor.id,
        )
        if notification is None:
            raise ResourceNotFoundError("Notification not found")
        if not notification.is_read:
            notification.is_read = True
            notification.read_at = datetime.now(UTC)
        await self.session.commit()
        return NotificationResponse.model_validate(notification)

    async def mark_all_read(self, actor: User) -> ReadAllResponse:
        count = await self.repository.mark_all_read(
            actor.id,
            now=datetime.now(UTC),
        )
        await self.session.commit()
        return ReadAllResponse(updated_count=count)

    async def delete(
        self,
        notification_id: UUID,
        actor: User,
    ) -> None:
        notification = await self.repository.get_owned_for_update(
            notification_id,
            actor.id,
        )
        if notification is None:
            raise ResourceNotFoundError("Notification not found")
        await self.session.delete(notification)
        await self.session.commit()

    async def create_due_notifications(
        self,
        *,
        now: datetime | None = None,
    ) -> int:
        current = now or datetime.now(UTC)
        candidates = await self.repository.due_notification_candidates(
            now=current,
            due_before=current + timedelta(days=7),
        )
        created = 0
        for task in candidates:
            if task.assignee_id is None or task.due_date is None:
                continue
            due_date = self._as_utc(task.due_date)
            notification_type = (
                NotificationType.TASK_OVERDUE
                if due_date < current
                else NotificationType.TASK_DUE_SOON
            )
            dedupe_key = (
                f"{notification_type.value}:{task.id}:"
                f"{due_date.isoformat()}"
            )
            notification = Notification(
                user_id=task.assignee_id,
                type=notification_type,
                title=(
                    "Task overdue"
                    if notification_type is NotificationType.TASK_OVERDUE
                    else "Task due soon"
                ),
                message=(
                    "An assigned task is overdue."
                    if notification_type is NotificationType.TASK_OVERDUE
                    else "An assigned task is due within seven days."
                ),
                entity_type="task",
                entity_id=task.id,
                dedupe_key=dedupe_key,
            )
            try:
                async with self.session.begin_nested():
                    self.session.add(notification)
                    await self.session.flush()
            except IntegrityError as error:
                if not await self.repository.dedupe_key_exists(
                    dedupe_key
                ):
                    raise error
                continue
            created += 1
        await self.session.commit()
        return created

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
