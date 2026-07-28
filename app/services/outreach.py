from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any, TypeVar, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    ApprovalStatus,
    AuditAction,
    AuditEntity,
    AutomationRule,
    AutomationAction,
    AutomationTrigger,
    Campaign,
    Contact,
    DeliveryEvent,
    DeliveryEventType,
    DraftStatus,
    DynamicAudience,
    EmailAttachment,
    EmailDraft,
    EmailTemplate,
    EmailTemplateVersion,
    MailboxConnection,
    NotificationType,
    OutreachApproval,
    OutreachStatus,
    QueueStatus,
    SendQueueItem,
    Sequence,
    SequenceEnrollment,
    SequenceExecution,
    SequenceStep,
    User,
)
from app.database.models.base import Base
from app.database.repositories.crm import CRMRepository
from app.database.repositories.outreach import OutreachRepository
from app.database.repositories.workspace import WorkspaceMembershipRepository
from app.schemas.outreach import (
    ApprovalDecision,
    ApprovalRequest,
    AudienceCreate,
    AutomationCreate,
    CampaignCreate,
    DeliveryEventCreate,
    DraftCreate,
    EnrollRequest,
    MailboxCreate,
    ScheduleRequest,
    SequenceCreate,
    SequenceStepCreate,
    TemplateCreate,
    TemplateVersionCreate,
)
from app.services.audit import record_audit
from app.services.cache import (
    crm_analytics_cache,
    dashboard_cache,
    outreach_analytics_cache,
)
from app.services.exceptions import (
    PermissionDeniedError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from app.services.mailbox_providers import (
    MailboxAdapter,
    OutboundAttachment,
    OutboundMessage,
)
from app.services.notification import enqueue_crm_notification
from app.services.permissions import (
    can_approve_outreach,
    can_manage_outreach,
    crm_scope,
    has_full_access,
    outreach_scope,
)

UTC = timezone.utc
OutreachEntity = TypeVar("OutreachEntity", bound=Base)


class OutreachService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repository = OutreachRepository(session)
        self.workspace_memberships = WorkspaceMembershipRepository(session)

    async def create_mailbox(
        self, data: MailboxCreate, actor: User
    ) -> MailboxConnection:
        self._require_manage(actor)
        mailbox = MailboxConnection(
            **data.model_dump(),
            owner_id=actor.id,
            created_by=actor.id,
        )
        await self._create(mailbox, actor, AuditEntity.MAILBOX)
        return mailbox

    async def create_template(self, data: TemplateCreate, actor: User) -> EmailTemplate:
        self._require_manage(actor)
        template = EmailTemplate(
            name=data.name,
            description=data.description,
            owner_id=actor.id,
            created_by=actor.id,
        )
        self.session.add(template)
        try:
            await self.session.flush()
            self.session.add(
                EmailTemplateVersion(
                    template_id=template.id,
                    version=1,
                    subject=data.subject,
                    body_html=data.body_html,
                    body_text=data.body_text,
                    variables=self._variables(data.variables),
                    created_by=actor.id,
                )
            )
            record_audit(
                self.session,
                actor_id=actor.id,
                action=AuditAction.CREATE,
                entity=AuditEntity.EMAIL_TEMPLATE,
                entity_id=template.id,
            )
            await self.session.commit()
        except IntegrityError as error:
            await self.session.rollback()
            raise ResourceConflictError("Template already exists") from error
        await self.session.refresh(template, attribute_names=["versions"])
        await self._invalidate()
        return template

    async def add_template_version(
        self,
        template_id: UUID,
        data: TemplateVersionCreate,
        actor: User,
    ) -> EmailTemplateVersion:
        template = await self._owned(EmailTemplate, template_id, actor, for_update=True)
        version = EmailTemplateVersion(
            template_id=template.id,
            version=await self.repository.next_template_version(template.id),
            subject=data.subject,
            body_html=data.body_html,
            body_text=data.body_text,
            variables=self._variables(data.variables),
            created_by=actor.id,
        )
        self.session.add(version)
        try:
            await self.session.flush()
            record_audit(
                self.session,
                actor_id=actor.id,
                action=AuditAction.UPDATE,
                entity=AuditEntity.EMAIL_TEMPLATE,
                entity_id=template.id,
            )
            await self.session.commit()
        except IntegrityError as error:
            await self.session.rollback()
            raise ResourceConflictError("Template version conflict") from error
        return version

    async def create_draft(self, data: DraftCreate, actor: User) -> EmailDraft:
        self._require_manage(actor)
        await self._owned(MailboxConnection, data.mailbox_id, actor)
        if data.contact_id is not None:
            contact = await CRMRepository(self.session).get_visible(
                Contact, data.contact_id, crm_scope(actor)
            )
            if contact is None or contact.email is None:
                raise ResourceNotFoundError("Contact not found")
        subject = data.subject
        body_html = data.body_html
        body_text = data.body_text
        if data.template_version_id is not None:
            version = await self.repository.template_version_visible(
                data.template_version_id, outreach_scope(actor)
            )
            if version is None:
                raise ResourceNotFoundError("Template version not found")
            subject = self._render(
                version.subject, version.variables, data.variable_values
            )
            body_html = self._render(
                version.body_html,
                version.variables,
                data.variable_values,
                html=True,
            )
            body_text = (
                self._render(
                    version.body_text,
                    version.variables,
                    data.variable_values,
                )
                if version.body_text is not None
                else None
            )
        status = (
            DraftStatus.SCHEDULED
            if data.scheduled_at is not None
            else DraftStatus.DRAFT
        )
        draft = EmailDraft(
            **data.model_dump(
                exclude={
                    "attachments",
                    "subject",
                    "body_html",
                    "body_text",
                    "cc",
                    "bcc",
                    "to_email",
                }
            ),
            subject=subject,
            body_html=body_html,
            body_text=body_text,
            cc=[str(item).lower() for item in data.cc],
            bcc=[str(item).lower() for item in data.bcc],
            to_email=str(data.to_email).lower(),
            status=status,
            owner_id=actor.id,
            created_by=actor.id,
        )
        draft.attachments = [
            EmailAttachment(**attachment.model_dump())
            for attachment in data.attachments
        ]
        self.session.add(draft)
        await self.session.flush()
        if status is DraftStatus.SCHEDULED:
            self._queue(draft, data.scheduled_at)
        await self._commit_audit(
            actor, AuditAction.CREATE, AuditEntity.EMAIL_DRAFT, draft.id
        )
        return draft

    async def schedule_draft(
        self,
        draft_id: UUID,
        data: ScheduleRequest,
        actor: User,
    ) -> EmailDraft:
        draft = await self._owned(EmailDraft, draft_id, actor, for_update=True)
        if draft.status not in {
            DraftStatus.DRAFT,
            DraftStatus.APPROVED,
            DraftStatus.SCHEDULED,
        }:
            raise ResourceConflictError("Draft cannot be scheduled")
        if data.scheduled_at <= datetime.now(UTC):
            raise ResourceConflictError("Schedule time must be in the future")
        draft.scheduled_at = data.scheduled_at
        draft.status = DraftStatus.SCHEDULED
        self._queue(draft, data.scheduled_at)
        await self._commit_audit(
            actor, AuditAction.STATUS_CHANGE, AuditEntity.EMAIL_DRAFT, draft.id
        )
        return draft

    async def request_approval(
        self, draft_id: UUID, data: ApprovalRequest, actor: User
    ) -> OutreachApproval:
        draft = await self._owned(EmailDraft, draft_id, actor, for_update=True)
        reviewer = await self.session.get(User, data.reviewer_id)
        if reviewer is None or not can_approve_outreach(reviewer):
            raise ResourceNotFoundError("Reviewer not found")
        if data.reviewer_id == actor.id:
            raise ResourceConflictError("Approval requires another reviewer")
        if not await self.workspace_memberships.shares_workspace(
            actor.id,
            data.reviewer_id,
        ):
            raise PermissionDeniedError
        existing = await self.repository.pending_approval(draft.id)
        if existing is not None:
            if existing.status is ApprovalStatus.PENDING:
                return existing
            existing.status = ApprovalStatus.PENDING
            existing.requested_by = actor.id
            existing.reviewer_id = data.reviewer_id
            existing.decided_at = None
            existing.decision_reason = None
            approval = existing
        else:
            approval = OutreachApproval(
                draft_id=draft.id,
                requested_by=actor.id,
                reviewer_id=data.reviewer_id,
            )
            self.session.add(approval)
        draft.status = DraftStatus.PENDING_APPROVAL
        await self.session.flush()
        enqueue_crm_notification(
            self.session,
            user_id=data.reviewer_id,
            notification_type=NotificationType.OUTREACH_APPROVAL_REQUESTED,
            entity_type="email_draft",
            entity_id=draft.id,
            title="Outreach approval requested",
            message="An outreach draft is awaiting your approval.",
            dedupe_key=f"outreach-approval-request:{approval.id}",
        )
        await self._commit_audit(
            actor, AuditAction.STATUS_CHANGE, AuditEntity.EMAIL_DRAFT, draft.id
        )
        return approval

    async def decide_approval(
        self,
        approval_id: UUID,
        data: ApprovalDecision,
        actor: User,
    ) -> OutreachApproval:
        if not can_approve_outreach(actor):
            raise PermissionDeniedError
        approval = await self.session.scalar(
            select(OutreachApproval)
            .where(
                OutreachApproval.id == approval_id,
                OutreachApproval.reviewer_id == actor.id,
            )
            .with_for_update()
        )
        if approval is None:
            raise ResourceNotFoundError("Approval not found")
        if approval.status is not ApprovalStatus.PENDING:
            return approval
        draft = await self.session.get(EmailDraft, approval.draft_id)
        if draft is None:
            raise ResourceNotFoundError("Draft not found")
        if (
            draft.owner_id != approval.requested_by
            or not await self.workspace_memberships.shares_workspace(
                actor.id,
                draft.owner_id,
            )
        ):
            raise ResourceNotFoundError("Approval not found")
        approval.status = (
            ApprovalStatus.APPROVED if data.approved else ApprovalStatus.REJECTED
        )
        approval.reviewer_id = actor.id
        approval.decided_at = datetime.now(UTC)
        approval.decision_reason = data.reason
        draft.status = DraftStatus.APPROVED if data.approved else DraftStatus.DRAFT
        enqueue_crm_notification(
            self.session,
            user_id=approval.requested_by,
            notification_type=NotificationType.OUTREACH_APPROVAL_DECIDED,
            entity_type="email_draft",
            entity_id=draft.id,
            title="Outreach approval decided",
            message=f"Your outreach draft was {approval.status.value}.",
            dedupe_key=f"outreach-approval:{approval.id}:{approval.status.value}",
        )
        await self.session.commit()
        await self._invalidate()
        return approval

    async def create_sequence(self, data: SequenceCreate, actor: User) -> Sequence:
        self._require_manage(actor)
        sequence = Sequence(**data.model_dump(), owner_id=actor.id, created_by=actor.id)
        await self._create(sequence, actor, AuditEntity.SEQUENCE)
        await self.session.refresh(sequence, attribute_names=["steps"])
        return sequence

    async def add_sequence_step(
        self,
        sequence_id: UUID,
        data: SequenceStepCreate,
        actor: User,
    ) -> SequenceStep:
        sequence = await self._owned(Sequence, sequence_id, actor, for_update=True)
        if sequence.status is not OutreachStatus.DRAFT:
            raise ResourceConflictError("Only draft sequences can change")
        if (
            await self.repository.template_version_visible(
                data.template_version_id, outreach_scope(actor)
            )
            is None
        ):
            raise ResourceNotFoundError("Template version not found")
        step = SequenceStep(sequence_id=sequence.id, **data.model_dump())
        self.session.add(step)
        try:
            await self.session.flush()
            await self._commit_audit(
                actor, AuditAction.UPDATE, AuditEntity.SEQUENCE, sequence.id
            )
        except IntegrityError as error:
            await self.session.rollback()
            raise ResourceConflictError("Sequence step conflict") from error
        return step

    async def create_audience(
        self, data: AudienceCreate, actor: User
    ) -> DynamicAudience:
        self._require_manage(actor)
        allowed = {"status", "country", "company_id", "owner_id"}
        if not set(data.filter_definition).issubset(allowed):
            raise ResourceConflictError("Unsupported audience filter")
        filters = dict(data.filter_definition)
        if any(
            not isinstance(value, (str, int, float, bool)) for value in filters.values()
        ):
            raise ResourceConflictError("Audience filters must be scalar")
        for key in ("company_id", "owner_id"):
            if key in filters:
                try:
                    filters[key] = str(UUID(str(filters[key])))
                except ValueError as error:
                    raise ResourceConflictError(
                        "Invalid audience identifier"
                    ) from error
        audience = DynamicAudience(
            name=data.name,
            filter_definition=filters,
            owner_id=actor.id,
            created_by=actor.id,
        )
        await self._create(audience, actor, AuditEntity.CAMPAIGN)
        return audience

    async def create_campaign(self, data: CampaignCreate, actor: User) -> Campaign:
        self._require_manage(actor)
        for model, entity_id in (
            (Sequence, data.sequence_id),
            (DynamicAudience, data.audience_id),
            (MailboxConnection, data.mailbox_id),
        ):
            await self._owned(model, entity_id, actor)
        campaign = Campaign(**data.model_dump(), owner_id=actor.id, created_by=actor.id)
        await self._create(campaign, actor, AuditEntity.CAMPAIGN)
        return campaign

    async def launch_campaign(self, campaign_id: UUID, actor: User) -> Campaign:
        campaign = await self._owned(Campaign, campaign_id, actor, for_update=True)
        sequence = await self._owned(Sequence, campaign.sequence_id, actor)
        audience = await self._owned(DynamicAudience, campaign.audience_id, actor)
        if not sequence.steps:
            await self.session.refresh(sequence, attribute_names=["steps"])
        if not sequence.steps:
            raise ResourceConflictError("Sequence has no steps")
        contacts = await self.repository.audience_contacts(audience, crm_scope(actor))
        now = campaign.scheduled_at or datetime.now(UTC)
        for contact in contacts:
            self.session.add(
                SequenceEnrollment(
                    sequence_id=sequence.id,
                    campaign_id=campaign.id,
                    contact_id=contact.id,
                    current_step=0,
                    status=OutreachStatus.ACTIVE,
                    next_execution_at=now
                    + timedelta(minutes=sequence.steps[0].delay_minutes),
                    owner_id=actor.id,
                )
            )
        campaign.status = OutreachStatus.ACTIVE
        sequence.status = OutreachStatus.ACTIVE
        try:
            record_audit(
                self.session,
                actor_id=actor.id,
                action=AuditAction.STATUS_CHANGE,
                entity=AuditEntity.CAMPAIGN,
                entity_id=campaign.id,
            )
            await self.session.commit()
        except IntegrityError as error:
            await self.session.rollback()
            raise ResourceConflictError("Campaign enrollment conflict") from error
        await self._invalidate()
        return campaign

    async def enroll(
        self,
        sequence_id: UUID,
        data: EnrollRequest,
        actor: User,
    ) -> int:
        sequence = await self._owned(Sequence, sequence_id, actor)
        campaign = await self._owned(Campaign, data.campaign_id, actor)
        if campaign.sequence_id != sequence.id:
            raise ResourceConflictError("Campaign sequence mismatch")
        if not sequence.steps:
            await self.session.refresh(sequence, attribute_names=["steps"])
        if not sequence.steps:
            raise ResourceConflictError("Sequence has no steps")
        created = 0
        for contact_id in sorted(set(data.contact_ids)):
            contact = await CRMRepository(self.session).get_visible(
                Contact, contact_id, crm_scope(actor)
            )
            if contact is None or contact.email is None:
                raise ResourceNotFoundError("Contact not found")
            self.session.add(
                SequenceEnrollment(
                    sequence_id=sequence.id,
                    campaign_id=data.campaign_id,
                    contact_id=contact.id,
                    next_execution_at=datetime.now(UTC)
                    + timedelta(minutes=sequence.steps[0].delay_minutes),
                    owner_id=actor.id,
                )
            )
            created += 1
        try:
            record_audit(
                self.session,
                actor_id=actor.id,
                action=AuditAction.UPDATE,
                entity=AuditEntity.SEQUENCE,
                entity_id=sequence.id,
            )
            await self.session.commit()
        except IntegrityError as error:
            await self.session.rollback()
            raise ResourceConflictError("Contact already enrolled") from error
        return created

    async def process_enrollments(
        self,
        *,
        now: datetime | None = None,
        limit: int = 100,
    ) -> int:
        current = now or datetime.now(UTC)
        rows = await self.repository.claim_enrollments(now=current, limit=limit)
        created = 0
        for enrollment, contact, step, campaign, version in rows:
            values = {
                "first_name": contact.first_name,
                "last_name": contact.last_name,
                "email": contact.email or "",
            }
            supplied = {
                variable: values[variable]
                for variable in version.variables
                if variable in values
            }
            try:
                subject = self._render(version.subject, version.variables, supplied)
                body_html = self._render(
                    version.body_html,
                    version.variables,
                    supplied,
                    html=True,
                )
            except ResourceConflictError:
                enrollment.status = OutreachStatus.PAUSED
                continue
            draft = EmailDraft(
                mailbox_id=campaign.mailbox_id,
                template_version_id=version.id,
                contact_id=contact.id,
                to_email=contact.email,
                subject=subject,
                body_html=body_html,
                body_text=version.body_text,
                variable_values=supplied,
                status=(
                    DraftStatus.PENDING_APPROVAL
                    if step.requires_approval
                    else DraftStatus.QUEUED
                ),
                owner_id=enrollment.owner_id,
                created_by=enrollment.owner_id,
            )
            self.session.add(draft)
            await self.session.flush()
            queue_item: SendQueueItem | None = None
            if not step.requires_approval:
                queue_item = SendQueueItem(
                    draft_id=draft.id,
                    enrollment_id=enrollment.id,
                    status=QueueStatus.PENDING,
                    available_at=current,
                )
                self.session.add(queue_item)
                await self.session.flush()
            self.session.add(
                SequenceExecution(
                    enrollment_id=enrollment.id,
                    step_id=step.id,
                    queue_item_id=(queue_item.id if queue_item is not None else None),
                    status=(
                        QueueStatus.PENDING
                        if queue_item is not None
                        else QueueStatus.PROCESSING
                    ),
                )
            )
            enrollment.current_step += 1
            next_step = await self.session.scalar(
                select(SequenceStep).where(
                    SequenceStep.sequence_id == enrollment.sequence_id,
                    SequenceStep.position == enrollment.current_step,
                )
            )
            if next_step is None:
                enrollment.status = OutreachStatus.COMPLETED
                enrollment.next_execution_at = None
            else:
                enrollment.next_execution_at = current + timedelta(
                    minutes=next_step.delay_minutes
                )
            created += 1
        try:
            await self.session.commit()
        except IntegrityError as error:
            await self.session.rollback()
            raise ResourceConflictError("Sequence execution conflict") from error
        await self._invalidate()
        return created

    async def record_event(
        self, data: DeliveryEventCreate, actor: User
    ) -> DeliveryEvent:
        if await self.repository.event_exists(data.provider_event_id):
            event = await self.session.scalar(
                select(DeliveryEvent).where(
                    DeliveryEvent.provider_event_id == data.provider_event_id
                )
            )
            if event is None:
                raise ResourceConflictError("Delivery event conflict")
            return event
        queue_item = await self.session.get(SendQueueItem, data.queue_item_id)
        if queue_item is None:
            raise ResourceNotFoundError("Queue item not found")
        draft = await self._owned(EmailDraft, queue_item.draft_id, actor)
        if draft.id != queue_item.draft_id:
            raise ResourceNotFoundError("Queue item not found")
        event = DeliveryEvent(**data.model_dump())
        self.session.add(event)
        trigger = {
            DeliveryEventType.REPLIED: AutomationTrigger.EMAIL_REPLIED,
            DeliveryEventType.BOUNCED: AutomationTrigger.EMAIL_BOUNCED,
        }.get(data.type)
        if trigger is not None:
            await self._apply_automations(
                trigger,
                {
                    "draft_id": str(draft.id),
                    "contact_id": (
                        str(draft.contact_id) if draft.contact_id is not None else None
                    ),
                },
                actor,
            )
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            existing = await self.session.scalar(
                select(DeliveryEvent).where(
                    DeliveryEvent.provider_event_id == data.provider_event_id
                )
            )
            if existing is None:
                raise ResourceConflictError("Delivery event conflict")
            return existing
        await self._invalidate()
        return event

    async def process_queue(
        self,
        adapters: dict[object, MailboxAdapter],
        *,
        now: datetime | None = None,
        limit: int = 50,
    ) -> tuple[int, int]:
        current = now or datetime.now(UTC)
        items = await self.repository.claim_queue(now=current, limit=limit)
        await self.session.commit()
        sent = 0
        failed = 0
        for item in items:
            draft = await self.repository.draft_for_send(item.draft_id)
            if draft is None:
                item.status = QueueStatus.FAILED
                failed += 1
                continue
            mailbox = await self.session.get(MailboxConnection, draft.mailbox_id)
            if mailbox is None or not mailbox.is_active:
                item.status = QueueStatus.FAILED
                item.last_error_code = "mailbox_unavailable"
                self._notify_send_failure(draft)
                failed += 1
                continue
            if (
                await self.repository.sent_count(mailbox.id, now=current)
                >= mailbox.daily_send_limit
            ):
                item.status = QueueStatus.RETRY
                item.available_at = current + timedelta(hours=1)
                item.last_error_code = "rate_limited"
                continue
            adapter = adapters.get(mailbox.provider)
            if adapter is None:
                item.status = QueueStatus.RETRY
                item.available_at = current + timedelta(minutes=15)
                item.last_error_code = "provider_unavailable"
                continue
            try:
                result = await adapter.send(
                    OutboundMessage(
                        from_email=mailbox.email_address,
                        to_email=draft.to_email,
                        cc=tuple(draft.cc),
                        bcc=tuple(draft.bcc),
                        subject=draft.subject,
                        body_html=draft.body_html + (mailbox.signature_html or ""),
                        body_text=draft.body_text,
                        attachments=tuple(
                            OutboundAttachment(
                                filename=attachment.filename,
                                content_type=attachment.content_type,
                                size_bytes=attachment.size_bytes,
                                storage_key=attachment.storage_key,
                                checksum_sha256=attachment.checksum_sha256,
                            )
                            for attachment in draft.attachments
                        ),
                        idempotency_key=str(item.id),
                    ),
                    mailbox.credential_reference,
                )
                item.status = QueueStatus.SENT
                item.provider_message_id = result.message_id
                draft.status = DraftStatus.SENT
                sent += 1
            except Exception:
                item.last_error_code = "provider_error"
                if item.attempt_count >= item.max_attempts:
                    item.status = QueueStatus.FAILED
                    self._notify_send_failure(draft)
                    failed += 1
                else:
                    item.status = QueueStatus.RETRY
                    item.available_at = current + timedelta(
                        minutes=2**item.attempt_count
                    )
            execution = await self.session.scalar(
                select(SequenceExecution).where(
                    SequenceExecution.queue_item_id == item.id
                )
            )
            if execution is not None:
                execution.status = item.status
                if item.status is QueueStatus.SENT:
                    execution.executed_at = current
        await self.session.commit()
        await self._invalidate()
        return sent, failed

    async def create_automation(
        self, data: AutomationCreate, actor: User
    ) -> AutomationRule:
        self._require_manage(actor)
        self._validate_automation(data)
        if data.action is AutomationAction.SEND_NOTIFICATION:
            target_id = UUID(str(data.action_config["user_id"]))
            if await self.session.get(User, target_id) is None or (
                not has_full_access(actor) and target_id != actor.id
            ):
                raise ResourceNotFoundError("Automation target not found")
        else:
            await self._owned(
                Sequence,
                UUID(str(data.action_config["sequence_id"])),
                actor,
            )
            await self._owned(
                Campaign,
                UUID(str(data.action_config["campaign_id"])),
                actor,
            )
            if "contact_id" in data.action_config:
                contact = await CRMRepository(self.session).get_visible(
                    Contact,
                    UUID(str(data.action_config["contact_id"])),
                    crm_scope(actor),
                )
                if contact is None:
                    raise ResourceNotFoundError("Automation contact not found")
        rule = AutomationRule(
            **data.model_dump(), owner_id=actor.id, created_by=actor.id
        )
        await self._create(rule, actor, AuditEntity.AUTOMATION)
        return rule

    async def _apply_automations(
        self,
        trigger: AutomationTrigger,
        context: dict[str, object],
        actor: User,
    ) -> None:
        rules = await self.repository.active_automations(trigger, outreach_scope(actor))
        for rule in rules:
            if any(
                context.get(key) != expected
                for key, expected in rule.conditions.items()
            ):
                continue
            if rule.action is AutomationAction.SEND_NOTIFICATION:
                user_id = UUID(str(rule.action_config["user_id"]))
                enqueue_crm_notification(
                    self.session,
                    user_id=user_id,
                    notification_type=NotificationType.SYSTEM,
                    entity_type="automation",
                    entity_id=rule.id,
                    title=str(rule.action_config["title"]),
                    message=str(rule.action_config["message"]),
                    dedupe_key=(
                        f"outreach-automation:{rule.id}:"
                        f"{context.get('draft_id', 'event')}"
                    ),
                )
            elif rule.action is AutomationAction.ENROLL_SEQUENCE:
                sequence_id = UUID(str(rule.action_config["sequence_id"]))
                campaign_id = UUID(str(rule.action_config["campaign_id"]))
                contact_value = rule.action_config.get(
                    "contact_id", context.get("contact_id")
                )
                if contact_value is None:
                    continue
                self.session.add(
                    SequenceEnrollment(
                        sequence_id=sequence_id,
                        campaign_id=campaign_id,
                        contact_id=UUID(str(contact_value)),
                        next_execution_at=datetime.now(UTC),
                        owner_id=rule.owner_id,
                    )
                )

    @staticmethod
    def _validate_automation(data: AutomationCreate) -> None:
        required = (
            {"user_id", "title", "message"}
            if data.action is AutomationAction.SEND_NOTIFICATION
            else {"sequence_id", "campaign_id"}
        )
        if not required.issubset(data.action_config):
            raise ResourceConflictError("Automation action is incomplete")
        try:
            if data.action is AutomationAction.SEND_NOTIFICATION:
                if (
                    not str(data.action_config["title"]).strip()
                    or not str(data.action_config["message"]).strip()
                ):
                    raise ResourceConflictError(
                        "Automation notification cannot be blank"
                    )
                UUID(str(data.action_config["user_id"]))
            else:
                UUID(str(data.action_config["sequence_id"]))
                UUID(str(data.action_config["campaign_id"]))
                if "contact_id" in data.action_config:
                    UUID(str(data.action_config["contact_id"]))
        except (TypeError, ValueError) as error:
            raise ResourceConflictError("Automation identifiers are invalid") from error

    async def list_entities(
        self,
        model: type[OutreachEntity],
        actor: User,
        *,
        search: str | None,
        limit: int,
        offset: int,
    ) -> Any:
        return await self.repository.list_visible(
            model,
            outreach_scope(actor),
            search=search,
            limit=limit,
            offset=offset,
        )

    async def get_entity(
        self,
        model: type[OutreachEntity],
        entity_id: UUID,
        actor: User,
    ) -> OutreachEntity:
        return await self._owned(model, entity_id, actor)

    async def _owned(
        self,
        model: type[OutreachEntity],
        entity_id: UUID,
        actor: User,
        *,
        for_update: bool = False,
    ) -> OutreachEntity:
        entity = await self.repository.get_visible(
            model,
            entity_id,
            outreach_scope(actor),
            for_update=for_update,
        )
        if entity is None:
            raise ResourceNotFoundError("Outreach resource not found")
        if not has_full_access(actor):
            owner_id = getattr(entity, "owner_id", actor.id)
            if owner_id != actor.id:
                raise ResourceNotFoundError("Outreach resource not found")
        return entity

    async def _create(
        self,
        entity: OutreachEntity,
        actor: User,
        audit_entity: AuditEntity,
    ) -> None:
        self.session.add(entity)
        try:
            await self.session.flush()
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
            raise ResourceConflictError("Outreach resource conflict") from error
        await self._invalidate()

    async def _commit_audit(
        self,
        actor: User,
        action: AuditAction,
        entity: AuditEntity,
        entity_id: UUID,
    ) -> None:
        record_audit(
            self.session,
            actor_id=actor.id,
            action=action,
            entity=entity,
            entity_id=entity_id,
        )
        await self.session.commit()
        await self._invalidate()

    def _queue(self, draft: EmailDraft, available_at: datetime | None) -> None:
        self.session.add(
            SendQueueItem(
                draft_id=draft.id,
                status=QueueStatus.PENDING,
                available_at=available_at or datetime.now(UTC),
            )
        )

    def _notify_send_failure(self, draft: EmailDraft) -> None:
        enqueue_crm_notification(
            self.session,
            user_id=draft.owner_id,
            notification_type=NotificationType.OUTREACH_SEND_FAILED,
            entity_type="email_draft",
            entity_id=draft.id,
            title="Outreach send failed",
            message="An outreach message exhausted its delivery attempts.",
            dedupe_key=f"outreach-send-failed:{draft.id}",
        )

    @staticmethod
    def _variables(values: list[str]) -> list[str]:
        normalized = [item.strip() for item in values]
        if any(not item or not item.replace("_", "").isalnum() for item in normalized):
            raise ResourceConflictError("Invalid template variable")
        if len(set(normalized)) != len(normalized):
            raise ResourceConflictError("Duplicate template variable")
        return normalized

    @staticmethod
    def _render(
        value: str,
        variables: list[str],
        supplied: dict[str, str],
        *,
        html: bool = False,
    ) -> str:
        missing = set(variables) - set(supplied)
        extra = set(supplied) - set(variables)
        if missing or extra:
            raise ResourceConflictError("Template variables do not match")
        rendered = value
        for key in variables:
            replacement = escape(supplied[key]) if html else supplied[key]
            rendered = rendered.replace(f"{{{{{key}}}}}", replacement)
        return rendered

    @staticmethod
    def _require_manage(actor: User) -> None:
        if not can_manage_outreach(actor):
            raise PermissionDeniedError

    @staticmethod
    async def _invalidate() -> None:
        await dashboard_cache.invalidate()
        await crm_analytics_cache.invalidate()
        await outreach_analytics_cache.invalidate()
