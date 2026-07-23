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
    CRMActivity,
    CRMNote,
    Company,
    Contact,
    Deal,
    Lead,
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
        full_access = not frozenset(
            {"administrator", "executive"}
        ).isdisjoint(scope.roles)
        project_entities = (
            AuditEntity.PROJECT,
            AuditEntity.TASK,
        )
        crm_entities = tuple(
            item for item in AuditEntity if item not in project_entities
        )
        if scope.kind is VisibilityKind.GLOBAL:
            project_visibility: ColumnElement[bool] = true()
        elif scope.kind is VisibilityKind.OWNED:
            project_visibility = Project.owner_id == scope.user_id
        else:
            assigned_projects = select(Task.project_id).where(
                Task.assignee_id == scope.user_id
            )
            project_visibility = or_(
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
        if full_access:
            crm_visibility: ColumnElement[bool] = true()
        else:
            crm_visibility = or_(
                and_(
                    AuditLog.entity == AuditEntity.COMPANY,
                    AuditLog.entity_id.in_(
                        select(Company.id).where(
                            or_(
                                Company.owner_id == scope.user_id,
                                Company.created_by == scope.user_id,
                            )
                        )
                    ),
                ),
                and_(
                    AuditLog.entity == AuditEntity.CONTACT,
                    AuditLog.entity_id.in_(
                        select(Contact.id).where(
                            or_(
                                Contact.owner_id == scope.user_id,
                                Contact.created_by == scope.user_id,
                            )
                        )
                    ),
                ),
                and_(
                    AuditLog.entity == AuditEntity.LEAD,
                    AuditLog.entity_id.in_(
                        select(Lead.id).where(
                            or_(
                                Lead.owner_id == scope.user_id,
                                Lead.created_by == scope.user_id,
                            )
                        )
                    ),
                ),
                and_(
                    AuditLog.entity == AuditEntity.DEAL,
                    AuditLog.entity_id.in_(
                        select(Deal.id).where(
                            Deal.owner_id == scope.user_id
                        )
                    ),
                ),
                and_(
                    AuditLog.entity == AuditEntity.CRM_NOTE,
                    AuditLog.entity_id.in_(
                        select(CRMNote.id).where(
                            or_(
                                CRMNote.author_id == scope.user_id,
                                CRMNote.company_id.in_(
                                    select(Company.id).where(
                                        Company.owner_id == scope.user_id
                                    )
                                ),
                                CRMNote.contact_id.in_(
                                    select(Contact.id).where(
                                        Contact.owner_id == scope.user_id
                                    )
                                ),
                                CRMNote.lead_id.in_(
                                    select(Lead.id).where(
                                        Lead.owner_id == scope.user_id
                                    )
                                ),
                                CRMNote.deal_id.in_(
                                    select(Deal.id).where(
                                        Deal.owner_id == scope.user_id
                                    )
                                ),
                            )
                        )
                    ),
                ),
                and_(
                    AuditLog.entity == AuditEntity.CRM_ACTIVITY,
                    AuditLog.entity_id.in_(
                        select(CRMActivity.id).where(
                            or_(
                                CRMActivity.actor_id == scope.user_id,
                                CRMActivity.assigned_to == scope.user_id,
                                CRMActivity.company_id.in_(
                                    select(Company.id).where(
                                        Company.owner_id == scope.user_id
                                    )
                                ),
                                CRMActivity.contact_id.in_(
                                    select(Contact.id).where(
                                        Contact.owner_id == scope.user_id
                                    )
                                ),
                                CRMActivity.lead_id.in_(
                                    select(Lead.id).where(
                                        Lead.owner_id == scope.user_id
                                    )
                                ),
                                CRMActivity.deal_id.in_(
                                    select(Deal.id).where(
                                        Deal.owner_id == scope.user_id
                                    )
                                ),
                            )
                        )
                    ),
                ),
            )
        return or_(
            and_(
                AuditLog.entity.in_(project_entities),
                project_visibility,
            ),
            and_(
                AuditLog.entity.in_(crm_entities),
                crm_visibility,
            ),
        )
