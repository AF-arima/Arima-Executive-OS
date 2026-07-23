from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    ColumnElement,
    Select,
    and_,
    func,
    or_,
    select,
    true,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    AuditAction,
    AuditEntity,
    AuditLog,
    Project,
    Task,
)
from app.services.permissions import AnalyticsScope, VisibilityKind


@dataclass(frozen=True, slots=True)
class ActivityRow:
    id: UUID
    actor_id: UUID | None
    action: AuditAction
    entity: AuditEntity
    entity_id: UUID
    timestamp: datetime
    project_id: UUID | None


class ActivityRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_filtered(
        self,
        scope: AnalyticsScope,
        *,
        actor_id: UUID | None,
        entity: AuditEntity | None,
        action: AuditAction | None,
        project_id: UUID | None,
        start: datetime,
        end: datetime,
        limit: int,
        offset: int,
    ) -> tuple[list[ActivityRow], int]:
        statement, filters = self._base_statement(
            scope,
            actor_id=actor_id,
            entity=entity,
            action=action,
            project_id=project_id,
            start=start,
            end=end,
        )
        rows = await self.session.execute(
            statement.where(*filters)
            .order_by(AuditLog.timestamp.desc(), AuditLog.id.desc())
            .limit(limit)
            .offset(offset)
        )
        count = await self.session.scalar(
            select(func.count(AuditLog.id))
            .select_from(statement.get_final_froms()[0])
            .where(*filters)
        )
        return (
            [
                ActivityRow(
                    id=row[0],
                    actor_id=row[1],
                    action=row[2],
                    entity=row[3],
                    entity_id=row[4],
                    timestamp=row[5],
                    project_id=row[6],
                )
                for row in rows
            ],
            int(count or 0),
        )

    async def count_recent(
        self,
        scope: AnalyticsScope,
        *,
        start: datetime,
        end: datetime,
    ) -> int:
        statement, filters = self._base_statement(
            scope,
            actor_id=None,
            entity=None,
            action=None,
            project_id=None,
            start=start,
            end=end,
        )
        count = await self.session.scalar(
            select(func.count(AuditLog.id))
            .select_from(statement.get_final_froms()[0])
            .where(*filters)
        )
        return int(count or 0)

    def _base_statement(
        self,
        scope: AnalyticsScope,
        *,
        actor_id: UUID | None,
        entity: AuditEntity | None,
        action: AuditAction | None,
        project_id: UUID | None,
        start: datetime,
        end: datetime,
    ) -> tuple[Select[Any], list[ColumnElement[bool]]]:
        from_clause = AuditLog.__table__.outerjoin(
            Project,
            Project.id == AuditLog.project_id,
        )
        statement = select(
            AuditLog.id,
            AuditLog.actor_id,
            AuditLog.action,
            AuditLog.entity,
            AuditLog.entity_id,
            AuditLog.timestamp,
            AuditLog.project_id,
        ).select_from(from_clause)
        filters: list[ColumnElement[bool]] = [
            AuditLog.timestamp >= start,
            AuditLog.timestamp <= end,
            self._visibility_condition(scope),
        ]
        if actor_id is not None:
            filters.append(AuditLog.actor_id == actor_id)
        if entity is not None:
            filters.append(AuditLog.entity == entity)
        if action is not None:
            filters.append(AuditLog.action == action)
        if project_id is not None:
            filters.append(AuditLog.project_id == project_id)
        return statement, filters

    @staticmethod
    def _visibility_condition(
        scope: AnalyticsScope,
    ) -> ColumnElement[bool]:
        if scope.kind is VisibilityKind.GLOBAL:
            return true()
        if scope.kind is VisibilityKind.OWNED:
            return Project.owner_id == scope.user_id
        assigned_projects = select(Task.project_id).where(
            Task.assignee_id == scope.user_id
        )
        return or_(
            and_(
                AuditLog.entity == AuditEntity.PROJECT,
                AuditLog.project_id.in_(assigned_projects),
            ),
            and_(
                AuditLog.entity == AuditEntity.TASK,
                AuditLog.entity_id.in_(
                    select(Task.id).where(
                        Task.assignee_id == scope.user_id
                    )
                ),
            ),
        )
