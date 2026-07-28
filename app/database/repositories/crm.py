from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, TypeVar
from uuid import UUID

from sqlalchemy import ColumnElement, Select, false, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import (
    CRMActivity,
    CRMActivityType,
    CRMNote,
    Company,
    CompanyStatus,
    Contact,
    ContactStatus,
    Deal,
    DealStatus,
    Lead,
    LeadSource,
    LeadStatus,
    Pipeline,
    PipelineStage,
)
from app.database.models.base import Base
from app.database.repositories.base import AsyncRepository
from app.database.repositories.pagination import Page, escape_like, paginate
from app.schemas.common import SortDirection
from app.schemas.crm import CRMSortField
from app.services.permissions import AnalyticsScope, VisibilityKind

CRMModel = TypeVar("CRMModel", bound=Base)


@dataclass(frozen=True, slots=True)
class CompanyFilters:
    search: str | None = None
    status: CompanyStatus | None = None
    industry: str | None = None
    owner_id: UUID | None = None
    country: str | None = None
    include_archived: bool = False


@dataclass(frozen=True, slots=True)
class ContactFilters:
    search: str | None = None
    company_id: UUID | None = None
    status: ContactStatus | None = None
    owner_id: UUID | None = None
    include_archived: bool = False


@dataclass(frozen=True, slots=True)
class LeadFilters:
    search: str | None = None
    status: LeadStatus | None = None
    source: LeadSource | None = None
    owner_id: UUID | None = None
    company_id: UUID | None = None
    contact_id: UUID | None = None
    minimum_score: int | None = None
    follow_up_from: datetime | None = None
    follow_up_to: datetime | None = None
    include_archived: bool = False


@dataclass(frozen=True, slots=True)
class DealFilters:
    search: str | None = None
    pipeline_id: UUID | None = None
    stage_id: UUID | None = None
    status: DealStatus | None = None
    owner_id: UUID | None = None
    company_id: UUID | None = None
    primary_contact_id: UUID | None = None
    close_from: datetime | None = None
    close_to: datetime | None = None
    minimum_value: Decimal | None = None
    maximum_value: Decimal | None = None
    include_archived: bool = False


@dataclass(frozen=True, slots=True)
class NoteFilters:
    company_id: UUID | None = None
    contact_id: UUID | None = None
    lead_id: UUID | None = None
    deal_id: UUID | None = None
    author_id: UUID | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None


@dataclass(frozen=True, slots=True)
class CRMActivityFilters:
    type: CRMActivityType | None = None
    actor_id: UUID | None = None
    assigned_to: UUID | None = None
    company_id: UUID | None = None
    contact_id: UUID | None = None
    lead_id: UUID | None = None
    deal_id: UUID | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    completed: bool | None = None


class CRMRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.companies = AsyncRepository(Company, session)
        self.contacts = AsyncRepository(Contact, session)
        self.leads = AsyncRepository(Lead, session)
        self.pipelines = AsyncRepository(Pipeline, session)
        self.stages = AsyncRepository(PipelineStage, session)
        self.deals = AsyncRepository(Deal, session)
        self.notes = AsyncRepository(CRMNote, session)
        self.activities = AsyncRepository(CRMActivity, session)

    async def get_visible(
        self,
        model: type[CRMModel],
        entity_id: UUID,
        scope: AnalyticsScope,
        *,
        for_update: bool = False,
    ) -> CRMModel | None:
        statement = select(model).where(
            getattr(model, "id") == entity_id,
            self.visibility(model, scope),
        )
        if for_update:
            statement = statement.with_for_update()
        return await self.session.scalar(statement)

    async def get_pipeline(
        self,
        pipeline_id: UUID,
        *,
        created_by: UUID | None = None,
        for_update: bool = False,
    ) -> Pipeline | None:
        statement = (
            select(Pipeline)
            .where(Pipeline.id == pipeline_id)
            .options(selectinload(Pipeline.stages))
        )
        if created_by is not None:
            statement = statement.where(Pipeline.created_by == created_by)
        if for_update:
            statement = statement.with_for_update()
        return await self.session.scalar(statement)

    async def default_pipeline(self, created_by: UUID) -> Pipeline | None:
        return await self.session.scalar(
            select(Pipeline)
            .where(
                Pipeline.created_by == created_by,
                Pipeline.is_default.is_(True),
                Pipeline.is_active.is_(True),
            )
            .options(selectinload(Pipeline.stages))
            .with_for_update()
        )

    async def get_stage(
        self,
        stage_id: UUID,
        *,
        created_by: UUID | None = None,
        for_update: bool = False,
    ) -> PipelineStage | None:
        statement = select(PipelineStage).where(PipelineStage.id == stage_id)
        if created_by is not None:
            statement = statement.join(
                Pipeline,
                PipelineStage.pipeline_id == Pipeline.id,
            ).where(Pipeline.created_by == created_by)
        if for_update:
            statement = statement.with_for_update()
        return await self.session.scalar(statement)

    async def deal_for_originating_lead(
        self,
        lead_id: UUID,
    ) -> Deal | None:
        return await self.session.scalar(
            select(Deal).where(Deal.originating_lead_id == lead_id)
        )

    async def has_active_deals(
        self,
        *,
        pipeline_id: UUID | None = None,
        stage_id: UUID | None = None,
    ) -> bool:
        statement = select(Deal.id).where(
            Deal.archived_at.is_(None),
            Deal.status == DealStatus.OPEN,
        )
        if pipeline_id is not None:
            statement = statement.where(Deal.pipeline_id == pipeline_id)
        if stage_id is not None:
            statement = statement.where(Deal.stage_id == stage_id)
        return await self.session.scalar(statement.limit(1)) is not None

    async def list_companies(
        self,
        scope: AnalyticsScope,
        filters: CompanyFilters,
        *,
        limit: int,
        offset: int,
        sort_by: CRMSortField,
        direction: SortDirection,
    ) -> Page[Company]:
        statement = select(Company).where(self.visibility(Company, scope))
        if not filters.include_archived:
            statement = statement.where(Company.archived_at.is_(None))
        if filters.status is not None:
            statement = statement.where(Company.status == filters.status)
        if filters.industry:
            statement = statement.where(Company.industry == filters.industry)
        if filters.owner_id is not None:
            statement = statement.where(Company.owner_id == filters.owner_id)
        if filters.country:
            statement = statement.where(Company.country == filters.country)
        if filters.search:
            pattern = f"%{escape_like(filters.search.strip())}%"
            statement = statement.where(
                or_(
                    Company.name.ilike(pattern, escape="\\"),
                    Company.legal_name.ilike(pattern, escape="\\"),
                    Company.domain.ilike(pattern, escape="\\"),
                )
            )
        return await self._page(
            statement, Company, sort_by, direction, limit, offset
        )

    async def list_contacts(
        self,
        scope: AnalyticsScope,
        filters: ContactFilters,
        *,
        limit: int,
        offset: int,
        sort_by: CRMSortField,
        direction: SortDirection,
    ) -> Page[Contact]:
        statement = select(Contact).where(self.visibility(Contact, scope))
        if not filters.include_archived:
            statement = statement.where(Contact.archived_at.is_(None))
        if filters.company_id is not None:
            statement = statement.where(Contact.company_id == filters.company_id)
        if filters.status is not None:
            statement = statement.where(Contact.status == filters.status)
        if filters.owner_id is not None:
            statement = statement.where(Contact.owner_id == filters.owner_id)
        if filters.search:
            pattern = f"%{escape_like(filters.search.strip())}%"
            statement = statement.where(
                or_(
                    Contact.first_name.ilike(pattern, escape="\\"),
                    Contact.last_name.ilike(pattern, escape="\\"),
                    Contact.email.ilike(pattern, escape="\\"),
                )
            )
        return await self._page(
            statement, Contact, sort_by, direction, limit, offset
        )

    async def list_leads(
        self,
        scope: AnalyticsScope,
        filters: LeadFilters,
        *,
        limit: int,
        offset: int,
        sort_by: CRMSortField,
        direction: SortDirection,
    ) -> Page[Lead]:
        statement = select(Lead).where(self.visibility(Lead, scope))
        if not filters.include_archived:
            statement = statement.where(Lead.archived_at.is_(None))
        values: tuple[tuple[Any, Any], ...] = (
            (Lead.status, filters.status),
            (Lead.source, filters.source),
            (Lead.owner_id, filters.owner_id),
            (Lead.company_id, filters.company_id),
            (Lead.contact_id, filters.contact_id),
        )
        for column, value in values:
            if value is not None:
                statement = statement.where(column == value)
        if filters.minimum_score is not None:
            statement = statement.where(Lead.score >= filters.minimum_score)
        if filters.follow_up_from is not None:
            statement = statement.where(
                Lead.next_follow_up_at >= filters.follow_up_from
            )
        if filters.follow_up_to is not None:
            statement = statement.where(
                Lead.next_follow_up_at <= filters.follow_up_to
            )
        if filters.search:
            pattern = f"%{escape_like(filters.search.strip())}%"
            statement = statement.where(Lead.title.ilike(pattern, escape="\\"))
        return await self._page(
            statement, Lead, sort_by, direction, limit, offset
        )

    async def list_deals(
        self,
        scope: AnalyticsScope,
        filters: DealFilters,
        *,
        limit: int,
        offset: int,
        sort_by: CRMSortField,
        direction: SortDirection,
    ) -> Page[Deal]:
        statement = select(Deal).where(self.visibility(Deal, scope))
        if not filters.include_archived:
            statement = statement.where(Deal.archived_at.is_(None))
        values: tuple[tuple[Any, Any], ...] = (
            (Deal.pipeline_id, filters.pipeline_id),
            (Deal.stage_id, filters.stage_id),
            (Deal.status, filters.status),
            (Deal.owner_id, filters.owner_id),
            (Deal.company_id, filters.company_id),
            (Deal.primary_contact_id, filters.primary_contact_id),
        )
        for column, value in values:
            if value is not None:
                statement = statement.where(column == value)
        if filters.close_from is not None:
            statement = statement.where(
                Deal.expected_close_date >= filters.close_from
            )
        if filters.close_to is not None:
            statement = statement.where(
                Deal.expected_close_date <= filters.close_to
            )
        if filters.minimum_value is not None:
            statement = statement.where(Deal.value >= filters.minimum_value)
        if filters.maximum_value is not None:
            statement = statement.where(Deal.value <= filters.maximum_value)
        if filters.search:
            pattern = f"%{escape_like(filters.search.strip())}%"
            statement = statement.where(
                or_(
                    Deal.title.ilike(pattern, escape="\\"),
                    Deal.description.ilike(pattern, escape="\\"),
                )
            )
        return await self._page(
            statement, Deal, sort_by, direction, limit, offset
        )

    async def list_notes(
        self,
        scope: AnalyticsScope,
        filters: NoteFilters,
        *,
        limit: int,
        offset: int,
    ) -> Page[CRMNote]:
        statement = select(CRMNote).where(self.visibility(CRMNote, scope))
        for column, value in (
            (CRMNote.company_id, filters.company_id),
            (CRMNote.contact_id, filters.contact_id),
            (CRMNote.lead_id, filters.lead_id),
            (CRMNote.deal_id, filters.deal_id),
            (CRMNote.author_id, filters.author_id),
        ):
            if value is not None:
                statement = statement.where(column == value)
        if filters.start_date is not None:
            statement = statement.where(
                CRMNote.created_at >= filters.start_date
            )
        if filters.end_date is not None:
            statement = statement.where(CRMNote.created_at <= filters.end_date)
        statement = statement.order_by(
            CRMNote.created_at.desc(), CRMNote.id.desc()
        )
        return await paginate(
            self.session, statement, limit=limit, offset=offset
        )

    async def list_activities(
        self,
        scope: AnalyticsScope,
        filters: CRMActivityFilters,
        *,
        limit: int,
        offset: int,
        direction: SortDirection,
    ) -> Page[CRMActivity]:
        statement = select(CRMActivity).where(
            self.visibility(CRMActivity, scope)
        )
        for column, value in (
            (CRMActivity.type, filters.type),
            (CRMActivity.actor_id, filters.actor_id),
            (CRMActivity.assigned_to, filters.assigned_to),
            (CRMActivity.company_id, filters.company_id),
            (CRMActivity.contact_id, filters.contact_id),
            (CRMActivity.lead_id, filters.lead_id),
            (CRMActivity.deal_id, filters.deal_id),
        ):
            if value is not None:
                statement = statement.where(column == value)
        if filters.start_date is not None:
            statement = statement.where(
                CRMActivity.created_at >= filters.start_date
            )
        if filters.end_date is not None:
            statement = statement.where(
                CRMActivity.created_at <= filters.end_date
            )
        if filters.completed is True:
            statement = statement.where(CRMActivity.completed_at.is_not(None))
        elif filters.completed is False:
            statement = statement.where(CRMActivity.completed_at.is_(None))
        order = (
            CRMActivity.created_at.asc()
            if direction is SortDirection.ASC
            else CRMActivity.created_at.desc()
        )
        id_order = (
            CRMActivity.id.asc()
            if direction is SortDirection.ASC
            else CRMActivity.id.desc()
        )
        return await paginate(
            self.session,
            statement.order_by(order, id_order),
            limit=limit,
            offset=offset,
        )

    async def list_pipelines(self, created_by: UUID) -> list[Pipeline]:
        result = await self.session.scalars(
            select(Pipeline)
            .where(Pipeline.created_by == created_by)
            .options(selectinload(Pipeline.stages))
            .order_by(Pipeline.name, Pipeline.id)
        )
        return list(result.all())

    @staticmethod
    def visibility(
        model: type[Base],
        scope: AnalyticsScope,
    ) -> ColumnElement[bool]:
        if scope.kind is VisibilityKind.GLOBAL:
            return true()
        if model is Company:
            return or_(
                Company.owner_id == scope.user_id,
                Company.created_by == scope.user_id,
                Company.id.in_(
                    select(Lead.company_id).where(
                        Lead.owner_id == scope.user_id
                    )
                ),
                Company.id.in_(
                    select(Deal.company_id).where(
                        Deal.owner_id == scope.user_id
                    )
                ),
            )
        if model is Contact:
            return or_(
                Contact.owner_id == scope.user_id,
                Contact.created_by == scope.user_id,
                Contact.id.in_(
                    select(Lead.contact_id).where(
                        Lead.owner_id == scope.user_id
                    )
                ),
                Contact.id.in_(
                    select(Deal.primary_contact_id).where(
                        Deal.owner_id == scope.user_id
                    )
                ),
            )
        if model in (Lead, Deal):
            owner = getattr(model, "owner_id")
            creator = getattr(model, "created_by")
            return or_(owner == scope.user_id, creator == scope.user_id)
        if model is CRMNote:
            return or_(
                CRMNote.author_id == scope.user_id,
                CRMNote.company_id.in_(
                    select(Company.id).where(
                        or_(
                            Company.owner_id == scope.user_id,
                            Company.created_by == scope.user_id,
                        )
                    )
                ),
                CRMNote.contact_id.in_(
                    select(Contact.id).where(
                        or_(
                            Contact.owner_id == scope.user_id,
                            Contact.created_by == scope.user_id,
                        )
                    )
                ),
                CRMNote.lead_id.in_(
                    select(Lead.id).where(
                        or_(
                            Lead.owner_id == scope.user_id,
                            Lead.created_by == scope.user_id,
                        )
                    )
                ),
                CRMNote.deal_id.in_(
                    select(Deal.id).where(Deal.owner_id == scope.user_id)
                ),
            )
        if model is CRMActivity:
            return or_(
                CRMActivity.actor_id == scope.user_id,
                CRMActivity.assigned_to == scope.user_id,
                CRMActivity.company_id.in_(
                    select(Company.id).where(
                        or_(
                            Company.owner_id == scope.user_id,
                            Company.created_by == scope.user_id,
                        )
                    )
                ),
                CRMActivity.contact_id.in_(
                    select(Contact.id).where(
                        or_(
                            Contact.owner_id == scope.user_id,
                            Contact.created_by == scope.user_id,
                        )
                    )
                ),
                CRMActivity.lead_id.in_(
                    select(Lead.id).where(Lead.owner_id == scope.user_id)
                ),
                CRMActivity.deal_id.in_(
                    select(Deal.id).where(Deal.owner_id == scope.user_id)
                ),
            )
        return false()

    async def _page(
        self,
        statement: Select[tuple[CRMModel]],
        model: type[CRMModel],
        sort_by: CRMSortField,
        direction: SortDirection,
        limit: int,
        offset: int,
    ) -> Page[CRMModel]:
        columns = {
            CRMSortField.CREATED_AT: getattr(model, "created_at"),
            CRMSortField.UPDATED_AT: getattr(model, "updated_at"),
            CRMSortField.NAME: getattr(
                model, "name", getattr(model, "created_at")
            ),
            CRMSortField.TITLE: getattr(
                model, "title", getattr(model, "created_at")
            ),
            CRMSortField.VALUE: getattr(
                model, "value", getattr(model, "created_at")
            ),
            CRMSortField.EXPECTED_CLOSE_DATE: getattr(
                model, "expected_close_date", getattr(model, "created_at")
            ),
            CRMSortField.DUE_AT: getattr(
                model, "due_at", getattr(model, "created_at")
            ),
        }
        column = columns[sort_by]
        ordering = (
            column.asc().nulls_last()
            if direction is SortDirection.ASC
            else column.desc().nulls_last()
        )
        id_order = (
            getattr(model, "id").asc()
            if direction is SortDirection.ASC
            else getattr(model, "id").desc()
        )
        return await paginate(
            self.session,
            statement.order_by(ordering, id_order),
            limit=limit,
            offset=offset,
        )
