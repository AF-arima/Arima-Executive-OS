from typing import Annotated, TypeVar
from uuid import UUID

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.api.v1.dependencies import (
    AUTHENTICATED_RESPONSES,
    AnalyticsUser,
    SessionDependency,
)
from app.database.models import (
    AutomationRule,
    Campaign,
    DynamicAudience,
    EmailDraft,
    EmailTemplate,
    MailboxConnection,
    Sequence,
)
from app.database.models.base import Base
from app.database.repositories.pagination import Page
from app.schemas.outreach import (
    ApprovalDecision,
    ApprovalRequest,
    ApprovalResponse,
    AudienceCreate,
    AudienceResponse,
    AutomationCreate,
    AutomationResponse,
    CampaignCreate,
    CampaignResponse,
    DeliveryEventCreate,
    DraftCreate,
    DraftResponse,
    EnrollRequest,
    MailboxCreate,
    MailboxResponse,
    OutreachList,
    ScheduleRequest,
    SequenceCreate,
    SequenceResponse,
    SequenceStepCreate,
    SequenceStepResponse,
    TemplateCreate,
    TemplateResponse,
    TemplateVersionCreate,
    TemplateVersionResponse,
)
from app.services.outreach import OutreachService

router = APIRouter(prefix="/outreach", tags=["outreach"])
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]
Search = Annotated[str | None, Query(min_length=1, max_length=200)]
OutreachRow = TypeVar("OutreachRow", bound=Base)


@router.post(
    "/mailboxes",
    response_model=MailboxResponse,
    status_code=201,
    responses=AUTHENTICATED_RESPONSES,
)
async def create_mailbox(
    data: MailboxCreate, session: SessionDependency, actor: AnalyticsUser
) -> MailboxConnection:
    return await OutreachService(session).create_mailbox(data, actor)


@router.get("/mailboxes", response_model=OutreachList)
async def list_mailboxes(
    session: SessionDependency,
    actor: AnalyticsUser,
    limit: Limit = 50,
    offset: Offset = 0,
) -> OutreachList:
    page = await OutreachService(session).list_entities(
        MailboxConnection,
        actor,
        search=None,
        limit=limit,
        offset=offset,
    )
    return _page(page, MailboxResponse)


@router.get("/mailboxes/{mailbox_id}", response_model=MailboxResponse)
async def get_mailbox(
    mailbox_id: UUID,
    session: SessionDependency,
    actor: AnalyticsUser,
) -> MailboxConnection:
    return await OutreachService(session).get_entity(
        MailboxConnection, mailbox_id, actor
    )


@router.post("/templates", response_model=TemplateResponse, status_code=201)
async def create_template(
    data: TemplateCreate, session: SessionDependency, actor: AnalyticsUser
) -> EmailTemplate:
    return await OutreachService(session).create_template(data, actor)


@router.get("/templates", response_model=OutreachList)
async def list_templates(
    session: SessionDependency,
    actor: AnalyticsUser,
    search: Search = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> OutreachList:
    page = await OutreachService(session).list_entities(
        EmailTemplate,
        actor,
        search=search,
        limit=limit,
        offset=offset,
    )
    return _page(page, TemplateResponse)


@router.get("/templates/{template_id}", response_model=TemplateResponse)
async def get_template(
    template_id: UUID,
    session: SessionDependency,
    actor: AnalyticsUser,
) -> EmailTemplate:
    return await OutreachService(session).get_entity(EmailTemplate, template_id, actor)


@router.post(
    "/templates/{template_id}/versions",
    response_model=TemplateVersionResponse,
    status_code=201,
)
async def create_template_version(
    template_id: UUID,
    data: TemplateVersionCreate,
    session: SessionDependency,
    actor: AnalyticsUser,
) -> object:
    return await OutreachService(session).add_template_version(template_id, data, actor)


@router.post("/drafts", response_model=DraftResponse, status_code=201)
async def create_draft(
    data: DraftCreate, session: SessionDependency, actor: AnalyticsUser
) -> EmailDraft:
    return await OutreachService(session).create_draft(data, actor)


@router.get("/drafts", response_model=OutreachList)
async def list_drafts(
    session: SessionDependency,
    actor: AnalyticsUser,
    limit: Limit = 50,
    offset: Offset = 0,
) -> OutreachList:
    page = await OutreachService(session).list_entities(
        EmailDraft, actor, search=None, limit=limit, offset=offset
    )
    return _page(page, DraftResponse)


@router.get("/drafts/{draft_id}", response_model=DraftResponse)
async def get_draft(
    draft_id: UUID, session: SessionDependency, actor: AnalyticsUser
) -> EmailDraft:
    return await OutreachService(session).get_entity(EmailDraft, draft_id, actor)


@router.post("/drafts/{draft_id}/schedule", response_model=DraftResponse)
async def schedule_draft(
    draft_id: UUID,
    data: ScheduleRequest,
    session: SessionDependency,
    actor: AnalyticsUser,
) -> EmailDraft:
    return await OutreachService(session).schedule_draft(draft_id, data, actor)


@router.post(
    "/drafts/{draft_id}/approval",
    response_model=ApprovalResponse,
    status_code=201,
)
async def request_approval(
    draft_id: UUID,
    data: ApprovalRequest,
    session: SessionDependency,
    actor: AnalyticsUser,
) -> object:
    return await OutreachService(session).request_approval(draft_id, data, actor)


@router.post(
    "/approvals/{approval_id}/decision",
    response_model=ApprovalResponse,
)
async def decide_approval(
    approval_id: UUID,
    data: ApprovalDecision,
    session: SessionDependency,
    actor: AnalyticsUser,
) -> object:
    return await OutreachService(session).decide_approval(approval_id, data, actor)


@router.post("/sequences", response_model=SequenceResponse, status_code=201)
async def create_sequence(
    data: SequenceCreate, session: SessionDependency, actor: AnalyticsUser
) -> Sequence:
    return await OutreachService(session).create_sequence(data, actor)


@router.get("/sequences", response_model=OutreachList)
async def list_sequences(
    session: SessionDependency,
    actor: AnalyticsUser,
    search: Search = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> OutreachList:
    page = await OutreachService(session).list_entities(
        Sequence, actor, search=search, limit=limit, offset=offset
    )
    return _page(page, SequenceResponse)


@router.get("/sequences/{sequence_id}", response_model=SequenceResponse)
async def get_sequence(
    sequence_id: UUID,
    session: SessionDependency,
    actor: AnalyticsUser,
) -> Sequence:
    return await OutreachService(session).get_entity(Sequence, sequence_id, actor)


@router.post(
    "/sequences/{sequence_id}/steps",
    response_model=SequenceStepResponse,
    status_code=201,
)
async def add_sequence_step(
    sequence_id: UUID,
    data: SequenceStepCreate,
    session: SessionDependency,
    actor: AnalyticsUser,
) -> object:
    return await OutreachService(session).add_sequence_step(sequence_id, data, actor)


@router.post("/sequences/{sequence_id}/enroll")
async def enroll_sequence(
    sequence_id: UUID,
    data: EnrollRequest,
    session: SessionDependency,
    actor: AnalyticsUser,
) -> dict[str, int]:
    count = await OutreachService(session).enroll(sequence_id, data, actor)
    return {"enrolled": count}


@router.post("/audiences", response_model=AudienceResponse, status_code=201)
async def create_audience(
    data: AudienceCreate, session: SessionDependency, actor: AnalyticsUser
) -> DynamicAudience:
    return await OutreachService(session).create_audience(data, actor)


@router.get("/audiences", response_model=OutreachList)
async def list_audiences(
    session: SessionDependency,
    actor: AnalyticsUser,
    search: Search = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> OutreachList:
    page = await OutreachService(session).list_entities(
        DynamicAudience,
        actor,
        search=search,
        limit=limit,
        offset=offset,
    )
    return _page(page, AudienceResponse)


@router.get("/audiences/{audience_id}", response_model=AudienceResponse)
async def get_audience(
    audience_id: UUID,
    session: SessionDependency,
    actor: AnalyticsUser,
) -> DynamicAudience:
    return await OutreachService(session).get_entity(
        DynamicAudience, audience_id, actor
    )


@router.post("/campaigns", response_model=CampaignResponse, status_code=201)
async def create_campaign(
    data: CampaignCreate, session: SessionDependency, actor: AnalyticsUser
) -> Campaign:
    return await OutreachService(session).create_campaign(data, actor)


@router.get("/campaigns", response_model=OutreachList)
async def list_campaigns(
    session: SessionDependency,
    actor: AnalyticsUser,
    search: Search = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> OutreachList:
    page = await OutreachService(session).list_entities(
        Campaign, actor, search=search, limit=limit, offset=offset
    )
    return _page(page, CampaignResponse)


@router.get("/campaigns/{campaign_id}", response_model=CampaignResponse)
async def get_campaign(
    campaign_id: UUID,
    session: SessionDependency,
    actor: AnalyticsUser,
) -> Campaign:
    return await OutreachService(session).get_entity(Campaign, campaign_id, actor)


@router.post("/campaigns/{campaign_id}/launch", response_model=CampaignResponse)
async def launch_campaign(
    campaign_id: UUID,
    session: SessionDependency,
    actor: AnalyticsUser,
) -> Campaign:
    return await OutreachService(session).launch_campaign(campaign_id, actor)


@router.post("/automations", response_model=AutomationResponse, status_code=201)
async def create_automation(
    data: AutomationCreate,
    session: SessionDependency,
    actor: AnalyticsUser,
) -> AutomationRule:
    return await OutreachService(session).create_automation(data, actor)


@router.get("/automations", response_model=OutreachList)
async def list_automations(
    session: SessionDependency,
    actor: AnalyticsUser,
    search: Search = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> OutreachList:
    page = await OutreachService(session).list_entities(
        AutomationRule, actor, search=search, limit=limit, offset=offset
    )
    return _page(page, AutomationResponse)


@router.get(
    "/automations/{automation_id}",
    response_model=AutomationResponse,
)
async def get_automation(
    automation_id: UUID,
    session: SessionDependency,
    actor: AnalyticsUser,
) -> AutomationRule:
    return await OutreachService(session).get_entity(
        AutomationRule, automation_id, actor
    )


@router.post("/delivery-events", status_code=202)
async def delivery_event(
    data: DeliveryEventCreate,
    session: SessionDependency,
    actor: AnalyticsUser,
) -> dict[str, str]:
    event = await OutreachService(session).record_event(data, actor)
    return {"id": str(event.id)}


def _page(page: Page[OutreachRow], schema: type[BaseModel]) -> OutreachList:
    return OutreachList(
        items=[
            schema.model_validate(item).model_dump(mode="json") for item in page.items
        ],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )
