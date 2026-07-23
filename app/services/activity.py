from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import AuditAction, AuditEntity, User
from app.database.repositories import ActivityRepository
from app.schemas.activity import ActivityItem, ActivityList
from app.services.analytics import AnalyticsService, MAX_GENERAL_RANGE
from app.services.permissions import analytics_scope


class ActivityService:
    def __init__(self, session: AsyncSession) -> None:
        self.repository = ActivityRepository(session)

    async def list(
        self,
        actor: User,
        *,
        actor_id: UUID | None,
        entity: AuditEntity | None,
        action: AuditAction | None,
        project_id: UUID | None,
        start_date: datetime | None,
        end_date: datetime | None,
        limit: int,
        offset: int,
    ) -> ActivityList:
        now = datetime.now(timezone.utc)
        start, end = AnalyticsService.resolve_range(
            start_date,
            end_date,
            now=now,
            maximum=MAX_GENERAL_RANGE,
        )
        rows, total = await self.repository.list_filtered(
            analytics_scope(actor),
            actor_id=actor_id,
            entity=entity,
            action=action,
            project_id=project_id,
            start=start,
            end=end,
            limit=limit,
            offset=offset,
        )
        return ActivityList(
            items=[
                ActivityItem(
                    id=row.id,
                    actor_id=row.actor_id,
                    action=row.action,
                    entity=row.entity,
                    entity_id=row.entity_id,
                    timestamp=row.timestamp,
                    summary=self._summary(row.action, row.entity),
                    metadata={
                        "project_id": row.project_id,
                    },
                )
                for row in rows
            ],
            total=total,
            limit=limit,
            offset=offset,
        )

    @staticmethod
    def _summary(
        action: AuditAction,
        entity: AuditEntity,
    ) -> str:
        labels = {
            AuditAction.CREATE: "created",
            AuditAction.UPDATE: "updated",
            AuditAction.DELETE: "deleted",
            AuditAction.ASSIGNMENT: "changed assignment for",
            AuditAction.STATUS_CHANGE: "changed status for",
        }
        return f"{labels[action]} {entity.value}"
