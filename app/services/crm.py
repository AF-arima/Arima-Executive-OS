from datetime import datetime, timezone
from decimal import Decimal
from collections.abc import Callable
from typing import Any, TypeVar, cast
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    AuditAction,
    AuditEntity,
    CRMActivity,
    CRMNote,
    Company,
    Contact,
    Deal,
    DealStatus,
    Lead,
    LeadStatus,
    NotificationType,
    Pipeline,
    PipelineStage,
    User,
)
from app.database.models.base import Base
from app.database.repositories import (
    CRMActivityFilters,
    CRMRepository,
    CompanyFilters,
    ContactFilters,
    DealFilters,
    LeadFilters,
    NoteFilters,
    Page,
    UserRepository,
)
from app.schemas.common import SortDirection
from app.schemas.crm import (
    CRMActivityComplete,
    CRMActivityCreate,
    CRMActivityUpdate,
    CRMNoteCreate,
    CRMNoteUpdate,
    CRMSortField,
    CompanyCreate,
    CompanyUpdate,
    ContactCreate,
    ContactUpdate,
    DealCreate,
    DealStageUpdate,
    DealUpdate,
    LeadConvertRequest,
    LeadCreate,
    LeadUpdate,
    PipelineCreate,
    PipelineStageCreate,
    PipelineStageUpdate,
    PipelineUpdate,
    StageReorderRequest,
)
from app.services.audit import record_audit
from app.services.cache import crm_analytics_cache, dashboard_cache
from app.services.exceptions import (
    InvalidAnalyticsRequestError,
    PermissionDeniedError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from app.services.notification import enqueue_crm_notification
from app.services.permissions import (
    can_create_crm,
    can_contribute_crm,
    can_manage_crm_record,
    can_manage_pipelines,
    crm_scope,
    has_full_access,
    user_roles,
)

UTC = timezone.utc
CRMEntity = TypeVar("CRMEntity", bound=Base)
LEAD_TRANSITIONS = {
    LeadStatus.NEW: {
        LeadStatus.CONTACTED,
        LeadStatus.ENGAGED,
        LeadStatus.QUALIFIED,
        LeadStatus.LOST,
        LeadStatus.DISQUALIFIED,
    },
    LeadStatus.CONTACTED: {
        LeadStatus.ENGAGED,
        LeadStatus.QUALIFIED,
        LeadStatus.LOST,
        LeadStatus.DISQUALIFIED,
    },
    LeadStatus.ENGAGED: {
        LeadStatus.CONTACTED,
        LeadStatus.QUALIFIED,
        LeadStatus.LOST,
        LeadStatus.DISQUALIFIED,
    },
    LeadStatus.QUALIFIED: {
        LeadStatus.ENGAGED,
        LeadStatus.LOST,
        LeadStatus.DISQUALIFIED,
    },
    LeadStatus.LOST: {LeadStatus.CONTACTED, LeadStatus.ENGAGED},
    LeadStatus.DISQUALIFIED: {LeadStatus.CONTACTED},
    LeadStatus.CONVERTED: set(),
}
DEFAULT_STAGES = (
    ("New Opportunity", 0, 10, False, False),
    ("Discovery", 1, 25, False, False),
    ("Proposal", 2, 50, False, False),
    ("Negotiation", 3, 75, False, False),
    ("Won", 4, 100, True, True),
    ("Lost", 5, 0, True, False),
)


class CRMService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = CRMRepository(session)
        self.users = UserRepository(session)

    async def create_company(
        self, data: CompanyCreate, actor: User
    ) -> Company:
        self._require_create(actor)
        owner_id = await self._owner(data.owner_id, actor)
        company = Company(
            **data.model_dump(exclude={"owner_id"}),
            owner_id=owner_id,
            created_by=actor.id,
        )
        await self._persist(
            company, actor, AuditEntity.COMPANY, "Company domain already exists"
        )
        return company

    async def list_companies(
        self,
        actor: User,
        filters: CompanyFilters,
        *,
        limit: int,
        offset: int,
        sort_by: CRMSortField,
        direction: SortDirection,
    ) -> Page[Company]:
        return await self.repository.list_companies(
            crm_scope(actor),
            filters,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
            direction=direction,
        )

    async def get_company(self, company_id: UUID, actor: User) -> Company:
        return await self._visible(Company, company_id, actor)

    async def update_company(
        self, company_id: UUID, data: CompanyUpdate, actor: User
    ) -> Company:
        company = await self._mutable(Company, company_id, actor)
        await self._apply_owner_update(company, data, actor)
        await self._update(
            company,
            data,
            actor,
            AuditEntity.COMPANY,
            "Company domain already exists",
        )
        return company

    async def archive_company(self, company_id: UUID, actor: User) -> None:
        company = await self._mutable(Company, company_id, actor)
        await self._archive(company, actor, AuditEntity.COMPANY)

    async def create_contact(
        self, data: ContactCreate, actor: User
    ) -> Contact:
        self._require_create(actor)
        await self._require_related(Company, data.company_id, actor)
        owner_id = await self._owner(data.owner_id, actor)
        contact = Contact(
            **data.model_dump(exclude={"owner_id"}),
            owner_id=owner_id,
            created_by=actor.id,
        )
        await self._persist(
            contact, actor, AuditEntity.CONTACT, "Contact email already exists"
        )
        return contact

    async def list_contacts(
        self,
        actor: User,
        filters: ContactFilters,
        *,
        limit: int,
        offset: int,
        sort_by: CRMSortField,
        direction: SortDirection,
    ) -> Page[Contact]:
        return await self.repository.list_contacts(
            crm_scope(actor),
            filters,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
            direction=direction,
        )

    async def get_contact(self, contact_id: UUID, actor: User) -> Contact:
        return await self._visible(Contact, contact_id, actor)

    async def update_contact(
        self, contact_id: UUID, data: ContactUpdate, actor: User
    ) -> Contact:
        contact = await self._mutable(Contact, contact_id, actor)
        values = data.model_dump(exclude_unset=True)
        if "company_id" in values:
            await self._require_related(Company, values["company_id"], actor)
        await self._apply_owner_update(contact, data, actor)
        await self._update(
            contact,
            data,
            actor,
            AuditEntity.CONTACT,
            "Contact email already exists",
        )
        return contact

    async def archive_contact(self, contact_id: UUID, actor: User) -> None:
        contact = await self._mutable(Contact, contact_id, actor)
        await self._archive(contact, actor, AuditEntity.CONTACT)

    async def create_lead(self, data: LeadCreate, actor: User) -> Lead:
        self._require_create(actor)
        if data.status is LeadStatus.CONVERTED:
            raise ResourceConflictError(
                "Leads must use the conversion endpoint"
            )
        await self._require_related(Company, data.company_id, actor)
        await self._require_related(Contact, data.contact_id, actor)
        owner_id = await self._owner(data.owner_id, actor)
        now = datetime.now(UTC)
        lead = Lead(
            **data.model_dump(exclude={"owner_id", "loss_reason"}),
            owner_id=owner_id,
            created_by=actor.id,
            qualified_at=now if data.status is LeadStatus.QUALIFIED else None,
            lost_at=now if data.status is LeadStatus.LOST else None,
            loss_reason=data.loss_reason,
        )
        await self._persist(
            lead,
            actor,
            AuditEntity.LEAD,
            "Lead conflict",
            after_flush=lambda: self._enqueue_assignment(
                actor,
                owner_id,
                NotificationType.LEAD_ASSIGNED,
                "lead",
                lead.id,
            ),
        )
        return lead

    async def list_leads(
        self,
        actor: User,
        filters: LeadFilters,
        *,
        limit: int,
        offset: int,
        sort_by: CRMSortField,
        direction: SortDirection,
    ) -> Page[Lead]:
        self._validate_range(filters.follow_up_from, filters.follow_up_to)
        return await self.repository.list_leads(
            crm_scope(actor),
            filters,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
            direction=direction,
        )

    async def get_lead(self, lead_id: UUID, actor: User) -> Lead:
        return await self._visible(Lead, lead_id, actor)

    async def update_lead(
        self, lead_id: UUID, data: LeadUpdate, actor: User
    ) -> Lead:
        lead = await self._mutable(Lead, lead_id, actor)
        values = data.model_dump(exclude_unset=True)
        target_status = values.get("status", lead.status)
        if not isinstance(target_status, LeadStatus):
            raise ResourceConflictError("Lead status is required")
        if target_status != lead.status:
            self._validate_lead_transition(lead.status, target_status)
            now = datetime.now(UTC)
            if target_status is LeadStatus.QUALIFIED:
                lead.qualified_at = now
                self._enqueue_assignment(
                    actor,
                    lead.owner_id,
                    NotificationType.LEAD_QUALIFIED,
                    "lead",
                    lead.id,
                )
            if target_status is LeadStatus.LOST:
                lead.lost_at = now
            elif lead.status is LeadStatus.LOST:
                lead.lost_at = None
                lead.loss_reason = None
            record_audit(
                self.session,
                actor_id=actor.id,
                action=AuditAction.STATUS_CHANGE,
                entity=AuditEntity.LEAD,
                entity_id=lead.id,
            )
        old_owner = lead.owner_id
        await self._apply_owner_update(lead, data, actor)
        for parent_model, key in (
            (Company, "company_id"),
            (Contact, "contact_id"),
        ):
            if key in values:
                await self._require_related(parent_model, values[key], actor)
        if lead.owner_id != old_owner:
            self._enqueue_assignment(
                actor,
                lead.owner_id,
                NotificationType.LEAD_ASSIGNED,
                "lead",
                lead.id,
            )
        await self._update(lead, data, actor, AuditEntity.LEAD, "Lead conflict")
        return lead

    async def archive_lead(self, lead_id: UUID, actor: User) -> None:
        lead = await self._mutable(Lead, lead_id, actor)
        await self._archive(lead, actor, AuditEntity.LEAD)

    async def convert_lead(
        self,
        lead_id: UUID,
        data: LeadConvertRequest,
        actor: User,
    ) -> Deal:
        lead = await self._mutable(Lead, lead_id, actor)
        existing = await self.repository.deal_for_originating_lead(lead.id)
        if existing is not None:
            return existing
        if lead.status is not LeadStatus.QUALIFIED:
            raise ResourceConflictError("Only qualified leads can be converted")
        pipeline = (
            await self.repository.get_pipeline(
                data.pipeline_id,
                created_by=actor.id,
                for_update=True,
            )
            if data.pipeline_id is not None
            else await self._default_pipeline(actor)
        )
        if pipeline is None or not pipeline.is_active:
            raise ResourceNotFoundError("Pipeline not found")
        stage = (
            await self.repository.get_stage(
                data.stage_id,
                created_by=actor.id,
                for_update=True,
            )
            if data.stage_id is not None
            else next((item for item in pipeline.stages if not item.is_closed), None)
        )
        if stage is None or stage.pipeline_id != pipeline.id or stage.is_closed:
            raise ResourceConflictError("An open stage in the pipeline is required")
        company_id = data.company_id or lead.company_id
        contact_id = data.contact_id or lead.contact_id
        await self._require_related(Company, company_id, actor)
        await self._require_related(Contact, contact_id, actor)
        now = datetime.now(UTC)
        deal = Deal(
            pipeline_id=pipeline.id,
            stage_id=stage.id,
            company_id=company_id,
            primary_contact_id=contact_id,
            originating_lead_id=lead.id,
            title=data.deal_title or lead.title,
            value=data.value or lead.estimated_value or Decimal("0"),
            currency=lead.currency,
            probability=stage.probability,
            expected_close_date=data.expected_close_date,
            owner_id=lead.owner_id or actor.id,
            created_by=actor.id,
            status=DealStatus.OPEN,
        )
        self.session.add(deal)
        try:
            await self.session.flush()
            lead.status = LeadStatus.CONVERTED
            lead.converted_at = now
            record_audit(
                self.session,
                actor_id=actor.id,
                action=AuditAction.CONVERT,
                entity=AuditEntity.LEAD,
                entity_id=lead.id,
            )
            record_audit(
                self.session,
                actor_id=actor.id,
                action=AuditAction.CREATE,
                entity=AuditEntity.DEAL,
                entity_id=deal.id,
            )
            self._enqueue_assignment(
                actor,
                deal.owner_id,
                NotificationType.LEAD_CONVERTED,
                "deal",
                deal.id,
                dedupe_key=f"lead-converted:{lead.id}",
            )
            await self.session.commit()
        except IntegrityError as error:
            await self.session.rollback()
            existing = await self.repository.deal_for_originating_lead(lead_id)
            if existing is not None:
                return existing
            raise ResourceConflictError("Lead conversion conflict") from error
        await self._invalidate_caches()
        return deal

    async def create_pipeline(
        self, data: PipelineCreate, actor: User
    ) -> Pipeline:
        self._require_pipeline_admin(actor)
        if data.is_default:
            await self._clear_default(actor)
        pipeline = Pipeline(**data.model_dump(), created_by=actor.id)
        await self._persist(
            pipeline, actor, AuditEntity.PIPELINE, "Pipeline already exists"
        )
        await self.session.refresh(pipeline, attribute_names=["stages"])
        return pipeline

    async def list_pipelines(self, actor: User) -> list[Pipeline]:
        return await self.repository.list_pipelines(actor.id)

    async def get_pipeline(self, pipeline_id: UUID, actor: User) -> Pipeline:
        pipeline = await self.repository.get_pipeline(
            pipeline_id,
            created_by=actor.id,
        )
        if pipeline is None:
            raise ResourceNotFoundError("Pipeline not found")
        return pipeline

    async def update_pipeline(
        self, pipeline_id: UUID, data: PipelineUpdate, actor: User
    ) -> Pipeline:
        self._require_pipeline_admin(actor)
        pipeline = await self.repository.get_pipeline(
            pipeline_id,
            created_by=actor.id,
            for_update=True,
        )
        if pipeline is None:
            raise ResourceNotFoundError("Pipeline not found")
        values = data.model_dump(exclude_unset=True)
        if values.get("is_default") is True:
            await self._clear_default(actor, exclude_id=pipeline.id)
        if values.get("is_active") is False and pipeline.is_default:
            raise ResourceConflictError("Default pipeline cannot be deactivated")
        await self._update(
            pipeline,
            data,
            actor,
            AuditEntity.PIPELINE,
            "Pipeline already exists",
        )
        return pipeline

    async def delete_pipeline(self, pipeline_id: UUID, actor: User) -> None:
        self._require_pipeline_admin(actor)
        pipeline = await self.repository.get_pipeline(
            pipeline_id,
            created_by=actor.id,
            for_update=True,
        )
        if pipeline is None:
            raise ResourceNotFoundError("Pipeline not found")
        if pipeline.is_default:
            raise ResourceConflictError("Default pipeline cannot be deleted")
        if await self.repository.has_active_deals(pipeline_id=pipeline.id):
            raise ResourceConflictError("Pipeline has active deals")
        await self.session.delete(pipeline)
        record_audit(
            self.session,
            actor_id=actor.id,
            action=AuditAction.DELETE,
            entity=AuditEntity.PIPELINE,
            entity_id=pipeline.id,
        )
        await self.session.commit()
        await self._invalidate_caches()

    async def create_stage(
        self,
        pipeline_id: UUID,
        data: PipelineStageCreate,
        actor: User,
    ) -> PipelineStage:
        self._require_pipeline_admin(actor)
        pipeline = await self.repository.get_pipeline(
            pipeline_id,
            created_by=actor.id,
            for_update=True,
        )
        if pipeline is None:
            raise ResourceNotFoundError("Pipeline not found")
        if data.is_won and any(stage.is_won for stage in pipeline.stages):
            raise ResourceConflictError("Pipeline already has a won stage")
        stage = PipelineStage(pipeline_id=pipeline.id, **data.model_dump())
        await self._persist(
            stage, actor, AuditEntity.PIPELINE_STAGE, "Invalid stage ordering"
        )
        return stage

    async def update_stage(
        self,
        pipeline_id: UUID,
        stage_id: UUID,
        data: PipelineStageUpdate,
        actor: User,
    ) -> PipelineStage:
        self._require_pipeline_admin(actor)
        stage = await self.repository.get_stage(
            stage_id,
            created_by=actor.id,
            for_update=True,
        )
        if stage is None or stage.pipeline_id != pipeline_id:
            raise ResourceNotFoundError("Pipeline stage not found")
        values = data.model_dump(exclude_unset=True)
        is_won = values.get("is_won", stage.is_won)
        is_closed = values.get("is_closed", stage.is_closed)
        if is_won and not is_closed:
            raise ResourceConflictError("A won stage must be closed")
        if is_won and not stage.is_won:
            pipeline = await self.repository.get_pipeline(
                pipeline_id,
                created_by=actor.id,
                for_update=True,
            )
            if pipeline is not None and any(item.is_won for item in pipeline.stages):
                raise ResourceConflictError("Pipeline already has a won stage")
        await self._update(
            stage,
            data,
            actor,
            AuditEntity.PIPELINE_STAGE,
            "Invalid stage",
        )
        return stage

    async def delete_stage(
        self, pipeline_id: UUID, stage_id: UUID, actor: User
    ) -> None:
        self._require_pipeline_admin(actor)
        stage = await self.repository.get_stage(
            stage_id,
            created_by=actor.id,
            for_update=True,
        )
        if stage is None or stage.pipeline_id != pipeline_id:
            raise ResourceNotFoundError("Pipeline stage not found")
        if await self.repository.has_active_deals(stage_id=stage.id):
            raise ResourceConflictError("Stage has active deals")
        await self.session.delete(stage)
        record_audit(
            self.session,
            actor_id=actor.id,
            action=AuditAction.DELETE,
            entity=AuditEntity.PIPELINE_STAGE,
            entity_id=stage.id,
        )
        await self.session.commit()
        await self._invalidate_caches()

    async def reorder_stages(
        self,
        pipeline_id: UUID,
        data: StageReorderRequest,
        actor: User,
    ) -> list[PipelineStage]:
        self._require_pipeline_admin(actor)
        pipeline = await self.repository.get_pipeline(
            pipeline_id,
            created_by=actor.id,
            for_update=True,
        )
        if pipeline is None:
            raise ResourceNotFoundError("Pipeline not found")
        requested = {item.stage_id: item.position for item in data.stages}
        if set(requested) != {item.id for item in pipeline.stages}:
            raise ResourceConflictError("All pipeline stages must be supplied")
        if len(set(requested.values())) != len(requested):
            raise ResourceConflictError("Stage positions must be unique")
        temporary = max(requested.values()) + len(requested) + 1
        for index, stage in enumerate(pipeline.stages):
            stage.position = temporary + index
        await self.session.flush()
        for stage in pipeline.stages:
            stage.position = requested[stage.id]
        record_audit(
            self.session,
            actor_id=actor.id,
            action=AuditAction.UPDATE,
            entity=AuditEntity.PIPELINE,
            entity_id=pipeline.id,
        )
        await self._commit_conflict("Invalid stage ordering")
        await self._invalidate_caches()
        return sorted(pipeline.stages, key=lambda item: item.position)

    async def create_deal(self, data: DealCreate, actor: User) -> Deal:
        self._require_create(actor)
        owner_id = await self._owner(data.owner_id, actor)
        stage = await self._validate_stage(
            data.pipeline_id,
            data.stage_id,
            actor,
        )
        await self._require_related(Company, data.company_id, actor)
        await self._require_related(Contact, data.primary_contact_id, actor)
        status, close_date = self._deal_state(stage)
        deal = Deal(
            **data.model_dump(exclude={"owner_id", "probability", "lost_reason"}),
            owner_id=cast(UUID, owner_id),
            probability=(
                data.probability
                if data.probability is not None
                else stage.probability
            ),
            created_by=actor.id,
            status=status,
            actual_close_date=close_date,
            lost_reason=data.lost_reason if status is DealStatus.LOST else None,
        )
        await self._persist(
            deal,
            actor,
            AuditEntity.DEAL,
            "Deal conflict",
            after_flush=lambda: self._enqueue_assignment(
                actor,
                deal.owner_id,
                NotificationType.DEAL_ASSIGNED,
                "deal",
                deal.id,
            ),
        )
        return deal

    async def list_deals(
        self,
        actor: User,
        filters: DealFilters,
        *,
        limit: int,
        offset: int,
        sort_by: CRMSortField,
        direction: SortDirection,
    ) -> Page[Deal]:
        self._validate_range(filters.close_from, filters.close_to)
        if (
            filters.minimum_value is not None
            and filters.maximum_value is not None
            and filters.minimum_value > filters.maximum_value
        ):
            raise InvalidAnalyticsRequestError(
                "minimum_value must not exceed maximum_value"
            )
        return await self.repository.list_deals(
            crm_scope(actor),
            filters,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
            direction=direction,
        )

    async def get_deal(self, deal_id: UUID, actor: User) -> Deal:
        return await self._visible(Deal, deal_id, actor)

    async def update_deal(
        self, deal_id: UUID, data: DealUpdate, actor: User
    ) -> Deal:
        deal = await self._mutable(Deal, deal_id, actor)
        values = data.model_dump(exclude_unset=True)
        old_owner = deal.owner_id
        old_stage = deal.stage_id
        pipeline_id = values.get("pipeline_id", deal.pipeline_id)
        stage_id = values.get("stage_id", deal.stage_id)
        if not isinstance(pipeline_id, UUID) or not isinstance(stage_id, UUID):
            raise ResourceConflictError("Pipeline and stage are required")
        stage = await self._validate_stage(pipeline_id, stage_id, actor)
        await self._apply_owner_update(deal, data, actor)
        await self._apply_deal_stage(
            deal,
            stage,
            values,
            stage_changed=stage.id != old_stage,
        )
        if deal.owner_id != old_owner:
            self._enqueue_assignment(
                actor,
                deal.owner_id,
                NotificationType.DEAL_ASSIGNED,
                "deal",
                deal.id,
            )
        effective = data.model_copy(
            update={"lost_reason": deal.lost_reason}
        )
        await self._update(
            deal, effective, actor, AuditEntity.DEAL, "Deal conflict"
        )
        return deal

    async def move_deal_stage(
        self,
        deal_id: UUID,
        data: DealStageUpdate,
        actor: User,
    ) -> Deal:
        deal = await self._mutable(Deal, deal_id, actor)
        stage = await self._validate_stage(
            deal.pipeline_id,
            data.stage_id,
            actor,
        )
        old_stage = deal.stage_id
        await self._apply_deal_stage(
            deal,
            stage,
            data.model_dump(exclude_unset=True),
            stage_changed=stage.id != old_stage,
        )
        if old_stage != deal.stage_id:
            record_audit(
                self.session,
                actor_id=actor.id,
                action=AuditAction.STAGE_CHANGE,
                entity=AuditEntity.DEAL,
                entity_id=deal.id,
            )
            self._enqueue_assignment(
                actor,
                deal.owner_id,
                NotificationType.DEAL_STAGE_CHANGED,
                "deal",
                deal.id,
            )
        await self._commit_conflict("Deal stage conflict")
        await self._invalidate_caches()
        return deal

    async def archive_deal(self, deal_id: UUID, actor: User) -> None:
        deal = await self._mutable(Deal, deal_id, actor)
        await self._archive(deal, actor, AuditEntity.DEAL)

    async def create_note(
        self, data: CRMNoteCreate, actor: User
    ) -> CRMNote:
        self._require_contributor(actor)
        await self._require_parent(data, actor)
        note = CRMNote(**data.model_dump(), author_id=actor.id)
        await self._persist(note, actor, AuditEntity.CRM_NOTE, "Invalid note")
        return note

    async def list_notes(
        self,
        actor: User,
        filters: NoteFilters,
        *,
        limit: int,
        offset: int,
    ) -> Page[CRMNote]:
        self._validate_range(filters.start_date, filters.end_date)
        return await self.repository.list_notes(
            crm_scope(actor), filters, limit=limit, offset=offset
        )

    async def update_note(
        self, note_id: UUID, data: CRMNoteUpdate, actor: User
    ) -> CRMNote:
        note = await self._visible(
            CRMNote, note_id, actor, for_update=True
        )
        if note.author_id != actor.id and not has_full_access(actor):
            raise PermissionDeniedError
        await self._update(
            note, data, actor, AuditEntity.CRM_NOTE, "Invalid note"
        )
        return note

    async def delete_note(self, note_id: UUID, actor: User) -> None:
        note = await self._visible(
            CRMNote, note_id, actor, for_update=True
        )
        if note.author_id != actor.id and not has_full_access(actor):
            raise PermissionDeniedError
        await self.session.delete(note)
        record_audit(
            self.session,
            actor_id=actor.id,
            action=AuditAction.DELETE,
            entity=AuditEntity.CRM_NOTE,
            entity_id=note.id,
        )
        await self.session.commit()
        await self._invalidate_caches()

    async def create_activity(
        self, data: CRMActivityCreate, actor: User
    ) -> CRMActivity:
        self._require_contributor(actor)
        await self._require_parent(data, actor)
        if data.assigned_to is not None:
            if data.assigned_to != actor.id:
                raise PermissionDeniedError
            await self._require_user(data.assigned_to)
        activity = CRMActivity(**data.model_dump(), actor_id=actor.id)
        await self._persist(
            activity,
            actor,
            AuditEntity.CRM_ACTIVITY,
            "Invalid activity",
            after_flush=lambda: self._enqueue_assignment(
                actor,
                activity.assigned_to,
                NotificationType.CRM_ACTIVITY_ASSIGNED,
                "crm_activity",
                activity.id,
            ),
        )
        return activity

    async def list_activities(
        self,
        actor: User,
        filters: CRMActivityFilters,
        *,
        limit: int,
        offset: int,
        direction: SortDirection,
    ) -> Page[CRMActivity]:
        self._validate_range(filters.start_date, filters.end_date)
        return await self.repository.list_activities(
            crm_scope(actor),
            filters,
            limit=limit,
            offset=offset,
            direction=direction,
        )

    async def get_activity(
        self, activity_id: UUID, actor: User
    ) -> CRMActivity:
        return await self._visible(CRMActivity, activity_id, actor)

    async def update_activity(
        self,
        activity_id: UUID,
        data: CRMActivityUpdate,
        actor: User,
    ) -> CRMActivity:
        activity = await self._visible(
            CRMActivity, activity_id, actor, for_update=True
        )
        if (
            activity.actor_id != actor.id
            and activity.assigned_to != actor.id
            and not has_full_access(actor)
        ):
            raise PermissionDeniedError
        await self._require_parent(data, actor, allow_empty=True)
        old_assignee = activity.assigned_to
        values = data.model_dump(exclude_unset=True)
        if (
            "assigned_to" in values
            and values["assigned_to"] is not None
            and values["assigned_to"] != old_assignee
        ):
            if values["assigned_to"] != actor.id:
                raise PermissionDeniedError
            await self._require_user(values["assigned_to"])
        if values.get("assigned_to", old_assignee) != old_assignee:
            self._enqueue_assignment(
                actor,
                values.get("assigned_to"),
                NotificationType.CRM_ACTIVITY_ASSIGNED,
                "crm_activity",
                activity.id,
            )
        await self._update(
            activity,
            data,
            actor,
            AuditEntity.CRM_ACTIVITY,
            "Invalid activity",
        )
        return activity

    async def complete_activity(
        self,
        activity_id: UUID,
        data: CRMActivityComplete,
        actor: User,
    ) -> CRMActivity:
        activity = await self._visible(
            CRMActivity, activity_id, actor, for_update=True
        )
        if (
            activity.actor_id != actor.id
            and activity.assigned_to != actor.id
            and not has_full_access(actor)
        ):
            raise PermissionDeniedError
        if activity.completed_at is None:
            now = data.occurred_at or datetime.now(UTC)
            activity.completed_at = now
            activity.occurred_at = activity.occurred_at or now
            activity.outcome = data.outcome
            if activity.lead_id is not None:
                lead = await self.repository.get_visible(
                    Lead,
                    activity.lead_id,
                    crm_scope(actor),
                    for_update=True,
                )
                if lead is not None:
                    lead.last_contacted_at = now
                    lead.next_follow_up_at = None
            record_audit(
                self.session,
                actor_id=actor.id,
                action=AuditAction.COMPLETE,
                entity=AuditEntity.CRM_ACTIVITY,
                entity_id=activity.id,
            )
            await self.session.commit()
            await self._invalidate_caches()
        return activity

    async def delete_activity(
        self, activity_id: UUID, actor: User
    ) -> None:
        activity = await self._visible(
            CRMActivity, activity_id, actor, for_update=True
        )
        if activity.actor_id != actor.id and not has_full_access(actor):
            raise PermissionDeniedError
        await self.session.delete(activity)
        record_audit(
            self.session,
            actor_id=actor.id,
            action=AuditAction.DELETE,
            entity=AuditEntity.CRM_ACTIVITY,
            entity_id=activity.id,
        )
        await self.session.commit()
        await self._invalidate_caches()

    async def _visible(
        self,
        model: type[CRMEntity],
        entity_id: UUID,
        actor: User,
        *,
        for_update: bool = False,
    ) -> CRMEntity:
        entity = await self.repository.get_visible(
            model, entity_id, crm_scope(actor), for_update=for_update
        )
        if entity is None:
            raise ResourceNotFoundError("CRM resource not found")
        return entity

    async def _mutable(
        self,
        model: type[CRMEntity],
        entity_id: UUID,
        actor: User,
    ) -> CRMEntity:
        entity = await self._visible(
            model, entity_id, actor, for_update=True
        )
        owner_id = cast(UUID | None, getattr(entity, "owner_id", None))
        created_by = cast(UUID, getattr(entity, "created_by"))
        if not can_manage_crm_record(
            actor, owner_id=owner_id, created_by=created_by
        ):
            raise PermissionDeniedError
        if getattr(entity, "archived_at", None) is not None:
            raise ResourceConflictError("Archived CRM records are read-only")
        return entity

    async def _persist(
        self,
        entity: CRMEntity,
        actor: User,
        audit_entity: AuditEntity,
        conflict_message: str,
        after_flush: Callable[[], None] | None = None,
    ) -> None:
        self.session.add(entity)
        try:
            await self.session.flush()
            if after_flush is not None:
                after_flush()
            record_audit(
                self.session,
                actor_id=actor.id,
                action=AuditAction.CREATE,
                entity=audit_entity,
                entity_id=cast(UUID, getattr(entity, "id")),
            )
            await self.session.commit()
        except IntegrityError as error:
            await self.session.rollback()
            raise ResourceConflictError(conflict_message) from error
        await self._invalidate_caches()

    async def _update(
        self,
        entity: CRMEntity,
        data: Any,
        actor: User,
        audit_entity: AuditEntity,
        conflict_message: str,
    ) -> None:
        values = data.model_dump(exclude_unset=True)
        values.pop("owner_id", None)
        if not values:
            await self.session.rollback()
            return
        for field_name, value in values.items():
            setattr(entity, field_name, value)
        record_audit(
            self.session,
            actor_id=actor.id,
            action=AuditAction.UPDATE,
            entity=audit_entity,
            entity_id=cast(UUID, getattr(entity, "id")),
        )
        await self._commit_conflict(conflict_message)
        await self._invalidate_caches()

    async def _archive(
        self, entity: CRMEntity, actor: User, audit_entity: AuditEntity
    ) -> None:
        setattr(entity, "archived_at", datetime.now(UTC))
        record_audit(
            self.session,
            actor_id=actor.id,
            action=AuditAction.DELETE,
            entity=audit_entity,
            entity_id=cast(UUID, getattr(entity, "id")),
        )
        await self.session.commit()
        await self._invalidate_caches()

    @staticmethod
    async def _invalidate_caches() -> None:
        await dashboard_cache.invalidate()
        await crm_analytics_cache.invalidate()

    async def _apply_owner_update(
        self, entity: Any, data: Any, actor: User
    ) -> None:
        values = data.model_dump(exclude_unset=True)
        if "owner_id" not in values:
            return
        owner_id = values["owner_id"]
        if owner_id is not None:
            await self._require_user(owner_id)
        if owner_id != entity.owner_id and not has_full_access(actor):
            if "manager" not in user_roles(actor) or owner_id != actor.id:
                raise PermissionDeniedError
        entity.owner_id = owner_id

    async def _owner(
        self, requested: UUID | None, actor: User
    ) -> UUID | None:
        owner_id = requested or actor.id
        if requested is not None and requested != actor.id and not has_full_access(actor):
            raise PermissionDeniedError
        await self._require_user(owner_id)
        return owner_id

    async def _require_user(self, user_id: UUID) -> User:
        user = await self.users.get_with_roles(user_id)
        if user is None:
            raise ResourceNotFoundError("User not found")
        if not user.is_active:
            raise ResourceConflictError("Inactive users cannot be assigned")
        return user

    async def _require_related(
        self,
        model: type[CRMEntity],
        entity_id: UUID | None,
        actor: User,
    ) -> CRMEntity | None:
        if entity_id is None:
            return None
        return await self._visible(model, entity_id, actor)

    async def _require_parent(
        self, data: Any, actor: User, *, allow_empty: bool = False
    ) -> None:
        values = data.model_dump(exclude_unset=True)
        found = False
        for model, key in (
            (Company, "company_id"),
            (Contact, "contact_id"),
            (Lead, "lead_id"),
            (Deal, "deal_id"),
        ):
            if key in values and values[key] is not None:
                found = True
                await self._require_related(model, values[key], actor)
        if not found and not allow_empty:
            raise ResourceConflictError("A CRM parent is required")

    async def _validate_stage(
        self,
        pipeline_id: UUID,
        stage_id: UUID,
        actor: User,
    ) -> PipelineStage:
        stage = await self.repository.get_stage(
            stage_id,
            created_by=actor.id,
            for_update=True,
        )
        if stage is None or stage.pipeline_id != pipeline_id:
            raise ResourceConflictError("Stage does not belong to pipeline")
        return stage

    async def _apply_deal_stage(
        self,
        deal: Deal,
        stage: PipelineStage,
        values: dict[str, Any],
        *,
        stage_changed: bool,
    ) -> None:
        status, close_date = self._deal_state(stage)
        deal.pipeline_id = stage.pipeline_id
        deal.stage_id = stage.id
        deal.status = status
        deal.actual_close_date = close_date
        if "probability" in values and values["probability"] is not None:
            deal.probability = values["probability"]
        elif stage_changed:
            deal.probability = stage.probability
        if status is DealStatus.LOST:
            deal.lost_reason = values.get("lost_reason", deal.lost_reason)
        else:
            deal.lost_reason = None

    @staticmethod
    def _deal_state(
        stage: PipelineStage,
    ) -> tuple[DealStatus, datetime | None]:
        if not stage.is_closed:
            return DealStatus.OPEN, None
        return (
            DealStatus.WON if stage.is_won else DealStatus.LOST,
            datetime.now(UTC),
        )

    async def _default_pipeline(self, actor: User) -> Pipeline:
        pipeline = await self.repository.default_pipeline(actor.id)
        if pipeline is not None:
            return pipeline
        if not can_manage_pipelines(actor):
            raise ResourceConflictError("No default pipeline is configured")
        pipeline = Pipeline(
            name="Default Sales Pipeline",
            description="Default opportunity pipeline",
            is_default=True,
            is_active=True,
            created_by=actor.id,
        )
        self.session.add(pipeline)
        await self.session.flush()
        for name, position, probability, is_closed, is_won in DEFAULT_STAGES:
            self.session.add(
                PipelineStage(
                    pipeline_id=pipeline.id,
                    name=name,
                    position=position,
                    probability=probability,
                    is_closed=is_closed,
                    is_won=is_won,
                )
            )
        await self.session.flush()
        await self.session.refresh(pipeline, attribute_names=["stages"])
        record_audit(
            self.session,
            actor_id=actor.id,
            action=AuditAction.CREATE,
            entity=AuditEntity.PIPELINE,
            entity_id=pipeline.id,
        )
        return pipeline

    async def _clear_default(
        self,
        actor: User,
        exclude_id: UUID | None = None,
    ) -> None:
        current = await self.repository.default_pipeline(actor.id)
        if current is not None and current.id != exclude_id:
            current.is_default = False
            await self.session.flush()

    def _enqueue_assignment(
        self,
        actor: User,
        user_id: UUID | None,
        notification_type: NotificationType,
        entity_type: str,
        entity_id: UUID,
        *,
        dedupe_key: str | None = None,
    ) -> None:
        if user_id is None or user_id == actor.id:
            return
        enqueue_crm_notification(
            self.session,
            user_id=user_id,
            notification_type=notification_type,
            entity_type=entity_type,
            entity_id=entity_id,
            title=notification_type.value.replace("_", " ").title(),
            message="A CRM record relevant to you was updated.",
            dedupe_key=dedupe_key,
        )

    async def _commit_conflict(self, message: str) -> None:
        try:
            await self.session.commit()
        except IntegrityError as error:
            await self.session.rollback()
            raise ResourceConflictError(message) from error

    @staticmethod
    def _validate_lead_transition(
        current: LeadStatus, target: LeadStatus
    ) -> None:
        if target not in LEAD_TRANSITIONS[current]:
            raise ResourceConflictError(
                f"Invalid lead transition from {current.value} to {target.value}"
            )

    @staticmethod
    def _validate_range(
        start: datetime | None, end: datetime | None
    ) -> None:
        for value in (start, end):
            if value is not None and (
                value.tzinfo is None or value.utcoffset() is None
            ):
                raise InvalidAnalyticsRequestError(
                    "Date filters must include a timezone"
                )
        if start is not None and end is not None and start > end:
            raise InvalidAnalyticsRequestError(
                "Range start must not exceed range end"
            )

    @staticmethod
    def _require_create(actor: User) -> None:
        if not can_create_crm(actor):
            raise PermissionDeniedError

    @staticmethod
    def _require_contributor(actor: User) -> None:
        if not can_contribute_crm(actor):
            raise PermissionDeniedError

    @staticmethod
    def _require_pipeline_admin(actor: User) -> None:
        if not can_manage_pipelines(actor):
            raise PermissionDeniedError
