from datetime import datetime, timedelta
from typing import TypeVar, cast
from uuid import UUID

from sqlalchemy import ColumnElement, false, func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models import (
    Campaign,
    AutomationRule,
    AutomationTrigger,
    Contact,
    DeliveryEvent,
    DynamicAudience,
    EmailDraft,
    EmailTemplate,
    EmailTemplateVersion,
    OutreachApproval,
    OutreachStatus,
    QueueStatus,
    SendQueueItem,
    Sequence,
    SequenceEnrollment,
    SequenceStep,
)
from app.database.models.base import Base
from app.database.repositories.crm import CRMRepository
from app.database.repositories.pagination import Page, escape_like, paginate
from app.services.permissions import AnalyticsScope, VisibilityKind

OutreachModel = TypeVar("OutreachModel", bound=Base)


class OutreachRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_visible(
        self,
        model: type[OutreachModel],
        entity_id: UUID,
        scope: AnalyticsScope,
        *,
        for_update: bool = False,
    ) -> OutreachModel | None:
        statement = select(model).where(
            getattr(model, "id") == entity_id,
            self.visibility(model, scope),
        )
        if model is EmailTemplate:
            statement = statement.options(selectinload(EmailTemplate.versions))
        if model is EmailDraft:
            statement = statement.options(selectinload(EmailDraft.attachments))
        if model is Sequence:
            statement = statement.options(selectinload(Sequence.steps))
        if for_update:
            statement = statement.with_for_update()
        return await self.session.scalar(statement)

    async def list_visible(
        self,
        model: type[OutreachModel],
        scope: AnalyticsScope,
        *,
        search: str | None,
        limit: int,
        offset: int,
    ) -> Page[OutreachModel]:
        statement = select(model).where(self.visibility(model, scope))
        if search:
            column = getattr(model, "name", None)
            if column is not None:
                statement = statement.where(
                    column.ilike(f"%{escape_like(search.strip())}%", escape="\\")
                )
        if model is EmailTemplate:
            statement = statement.options(selectinload(EmailTemplate.versions))
        if model is EmailDraft:
            statement = statement.options(selectinload(EmailDraft.attachments))
        if model is Sequence:
            statement = statement.options(selectinload(Sequence.steps))
        statement = statement.order_by(
            getattr(model, "created_at").desc(),
            getattr(model, "id").desc(),
        )
        return await paginate(self.session, statement, limit=limit, offset=offset)

    async def next_template_version(self, template_id: UUID) -> int:
        value = await self.session.scalar(
            select(func.max(EmailTemplateVersion.version)).where(
                EmailTemplateVersion.template_id == template_id
            )
        )
        return int(value or 0) + 1

    async def template_version_visible(
        self, version_id: UUID, scope: AnalyticsScope
    ) -> EmailTemplateVersion | None:
        return await self.session.scalar(
            select(EmailTemplateVersion)
            .join(
                EmailTemplate,
                EmailTemplate.id == EmailTemplateVersion.template_id,
            )
            .where(
                EmailTemplateVersion.id == version_id,
                self.visibility(EmailTemplate, scope),
            )
        )

    async def audience_contacts(
        self, audience: DynamicAudience, scope: AnalyticsScope
    ) -> list[Contact]:
        statement = select(Contact).where(
            Contact.archived_at.is_(None),
            Contact.email.is_not(None),
            CRMRepository.visibility(Contact, scope),
        )
        allowed = {
            "status": Contact.status,
            "country": Contact.country,
            "company_id": Contact.company_id,
            "owner_id": Contact.owner_id,
        }
        for key, value in audience.filter_definition.items():
            column = allowed.get(key)
            if column is not None:
                if key in {"company_id", "owner_id"}:
                    value = UUID(str(value))
                statement = statement.where(column == value)
        rows = await self.session.scalars(statement.order_by(Contact.id).limit(10000))
        return list(rows.all())

    async def claim_queue(self, *, now: datetime, limit: int) -> list[SendQueueItem]:
        rows = await self.session.scalars(
            select(SendQueueItem)
            .where(
                or_(
                    SendQueueItem.status.in_((QueueStatus.PENDING, QueueStatus.RETRY)),
                    (
                        (SendQueueItem.status == QueueStatus.PROCESSING)
                        & (SendQueueItem.locked_at <= now - timedelta(minutes=15))
                    ),
                ),
                SendQueueItem.available_at <= now,
            )
            .order_by(SendQueueItem.available_at, SendQueueItem.id)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        items = list(rows.all())
        for item in items:
            item.status = QueueStatus.PROCESSING
            item.locked_at = now
            item.attempt_count += 1
        await self.session.flush()
        return items

    async def claim_enrollments(
        self, *, now: datetime, limit: int
    ) -> list[
        tuple[
            SequenceEnrollment,
            Contact,
            SequenceStep,
            Campaign,
            EmailTemplateVersion,
        ]
    ]:
        result = await self.session.execute(
            select(
                SequenceEnrollment,
                Contact,
                SequenceStep,
                Campaign,
                EmailTemplateVersion,
            )
            .join(Contact, Contact.id == SequenceEnrollment.contact_id)
            .join(
                SequenceStep,
                (SequenceStep.sequence_id == SequenceEnrollment.sequence_id)
                & (SequenceStep.position == SequenceEnrollment.current_step),
            )
            .join(
                Campaign,
                Campaign.id == SequenceEnrollment.campaign_id,
            )
            .join(
                EmailTemplateVersion,
                EmailTemplateVersion.id == SequenceStep.template_version_id,
            )
            .where(
                SequenceEnrollment.status == OutreachStatus.ACTIVE,
                SequenceEnrollment.next_execution_at <= now,
            )
            .order_by(
                SequenceEnrollment.next_execution_at,
                SequenceEnrollment.id,
            )
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
        return [
            (enrollment, contact, step, campaign, version)
            for enrollment, contact, step, campaign, version in result
        ]

    async def sent_count(self, mailbox_id: UUID, *, now: datetime) -> int:
        start = now - timedelta(days=1)
        count = await self.session.scalar(
            select(func.count(SendQueueItem.id))
            .join(EmailDraft, EmailDraft.id == SendQueueItem.draft_id)
            .where(
                EmailDraft.mailbox_id == mailbox_id,
                SendQueueItem.status == QueueStatus.SENT,
                SendQueueItem.updated_at >= start,
            )
        )
        return int(count or 0)

    async def draft_for_send(self, draft_id: UUID) -> EmailDraft | None:
        return await self.session.scalar(
            select(EmailDraft)
            .where(EmailDraft.id == draft_id)
            .options(selectinload(EmailDraft.attachments))
        )

    async def event_exists(self, provider_event_id: str) -> bool:
        return (
            await self.session.scalar(
                select(DeliveryEvent.id).where(
                    DeliveryEvent.provider_event_id == provider_event_id
                )
            )
            is not None
        )

    async def pending_approval(self, draft_id: UUID) -> OutreachApproval | None:
        return await self.session.scalar(
            select(OutreachApproval)
            .where(OutreachApproval.draft_id == draft_id)
            .with_for_update()
        )

    async def analytics(
        self, scope: AnalyticsScope
    ) -> tuple[
        dict[object, int],
        dict[object, int],
        dict[object, int],
    ]:
        draft_rows = await self.session.execute(
            select(EmailDraft.status, func.count(EmailDraft.id))
            .where(self.visibility(EmailDraft, scope))
            .group_by(EmailDraft.status)
        )
        queue_rows = await self.session.execute(
            select(SendQueueItem.status, func.count(SendQueueItem.id))
            .join(EmailDraft, EmailDraft.id == SendQueueItem.draft_id)
            .where(self.visibility(EmailDraft, scope))
            .group_by(SendQueueItem.status)
        )
        event_rows = await self.session.execute(
            select(DeliveryEvent.type, func.count(DeliveryEvent.id))
            .join(
                SendQueueItem,
                SendQueueItem.id == DeliveryEvent.queue_item_id,
            )
            .join(EmailDraft, EmailDraft.id == SendQueueItem.draft_id)
            .where(self.visibility(EmailDraft, scope))
            .group_by(DeliveryEvent.type)
        )
        return (
            {key: int(value) for key, value in draft_rows},
            {key: int(value) for key, value in queue_rows},
            {key: int(value) for key, value in event_rows},
        )

    async def active_automations(
        self, trigger: AutomationTrigger, scope: AnalyticsScope
    ) -> list[AutomationRule]:
        rows = await self.session.scalars(
            select(AutomationRule).where(
                AutomationRule.trigger == trigger,
                AutomationRule.is_active.is_(True),
                self.visibility(AutomationRule, scope),
            )
        )
        return list(rows.all())

    @staticmethod
    def visibility(model: type[Base], scope: AnalyticsScope) -> ColumnElement[bool]:
        if scope.kind is VisibilityKind.GLOBAL:
            return true()
        owner = getattr(model, "owner_id", None)
        if owner is not None:
            return cast(ColumnElement[bool], owner == scope.user_id)
        if model is OutreachApproval:
            return or_(
                OutreachApproval.requested_by == scope.user_id,
                OutreachApproval.reviewer_id == scope.user_id,
            )
        return false()
