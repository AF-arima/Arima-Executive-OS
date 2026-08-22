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
    project_id: UUID | None = None,
    event_type: str | None = None,
    event_metadata: dict[str, object] | None = None,
) -> None:
    session.add(
        AuditLog(
            actor_id=actor_id,
            action=action,
            entity=entity,
            entity_id=entity_id,
            project_id=project_id,
            event_type=event_type,
            event_metadata=event_metadata or {},
        )
    )
