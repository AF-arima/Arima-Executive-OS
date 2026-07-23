from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import AuditEntity, AuditLog
from app.database.repositories.base import AsyncRepository


class AuditLogRepository(AsyncRepository[AuditLog]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(AuditLog, session)

    async def list_for_entity(
        self,
        entity: AuditEntity,
        entity_id: UUID,
    ) -> list[AuditLog]:
        result = await self.session.scalars(
            select(AuditLog)
            .where(
                AuditLog.entity == entity,
                AuditLog.entity_id == entity_id,
            )
            .order_by(AuditLog.timestamp, AuditLog.id)
        )
        return list(result.all())
