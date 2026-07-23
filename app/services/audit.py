from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import AuditAction, AuditEntity, AuditLog


def record_audit(
    session: AsyncSession,
    *,
    actor_id: UUID,
    action: AuditAction,
    entity: AuditEntity,
    entity_id: UUID,
) -> None:
    session.add(
        AuditLog(
            actor_id=actor_id,
            action=action,
            entity=entity,
            entity_id=entity_id,
        )
    )
