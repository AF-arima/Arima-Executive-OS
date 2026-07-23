from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require_any_role
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
    User,
)
from app.database.repositories import (
    CRMActivityFilters,
    CompanyFilters,
    ContactFilters,
    DealFilters,
    LeadFilters,
    NoteFilters,
)
from app.database.session import get_session
from app.schemas.common import SortDirection
from app.schemas.crm import (
    CRMActivityComplete,
    CRMActivityCreate,
    CRMActivityResponse,
    CRMActivityUpdate,
    CRMListResponse,
    CRMNoteCreate,
    CRMNoteResponse,
    CRMNoteUpdate,
    CRMSortField,
    CompanyCreate,
    CompanyResponse,
    CompanyUpdate,
    ContactCreate,
    ContactResponse,
    ContactUpdate,
    DealCreate,
    DealResponse,
    DealStageUpdate,
    DealUpdate,
    LeadConvertRequest,
    LeadCreate,
    LeadResponse,
    LeadUpdate,
    PipelineCreate,
    PipelineResponse,
    PipelineStageCreate,
    PipelineStageResponse,
    PipelineStageUpdate,
    PipelineUpdate,
    StageReorderRequest,
)
from app.services.crm import CRMService

router = APIRouter(prefix="/crm", tags=["crm"])
SessionDependency = Annotated[AsyncSession, Depends(get_session)]
CurrentUser = Annotated[
    User,
    Depends(
        require_any_role(
            "administrator", "executive", "manager", "analyst", "viewer"
        )
    ),
]
Limit = Annotated[int, Query(ge=1, le=100)]
Offset = Annotated[int, Query(ge=0)]
Search = Annotated[str | None, Query(min_length=1, max_length=200)]


@router.post("/companies", response_model=CompanyResponse, status_code=201)
async def create_company(
    data: CompanyCreate,
    session: SessionDependency,
    actor: CurrentUser,
) -> Company:
    return await CRMService(session).create_company(data, actor)


@router.get("/companies", response_model=CRMListResponse)
async def list_companies(
    session: SessionDependency,
    actor: CurrentUser,
    search: Search = None,
    company_status: Annotated[
        CompanyStatus | None, Query(alias="status")
    ] = None,
    industry: str | None = None,
    owner_id: UUID | None = None,
    country: str | None = None,
    include_archived: bool = False,
    limit: Limit = 50,
    offset: Offset = 0,
    sort_by: CRMSortField = CRMSortField.CREATED_AT,
    direction: SortDirection = SortDirection.DESC,
) -> CRMListResponse:
    page = await CRMService(session).list_companies(
        actor,
        CompanyFilters(
            search, company_status, industry, owner_id, country, include_archived
        ),
        limit=limit,
        offset=offset,
        sort_by=sort_by,
        direction=direction,
    )
    return _page(page, CompanyResponse)


@router.get("/companies/{company_id}", response_model=CompanyResponse)
async def get_company(
    company_id: UUID, session: SessionDependency, actor: CurrentUser
) -> Company:
    return await CRMService(session).get_company(company_id, actor)


@router.patch("/companies/{company_id}", response_model=CompanyResponse)
async def update_company(
    company_id: UUID,
    data: CompanyUpdate,
    session: SessionDependency,
    actor: CurrentUser,
) -> Company:
    return await CRMService(session).update_company(company_id, data, actor)


@router.delete("/companies/{company_id}", status_code=204)
async def delete_company(
    company_id: UUID, session: SessionDependency, actor: CurrentUser
) -> Response:
    await CRMService(session).archive_company(company_id, actor)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/contacts", response_model=ContactResponse, status_code=201)
async def create_contact(
    data: ContactCreate, session: SessionDependency, actor: CurrentUser
) -> Contact:
    return await CRMService(session).create_contact(data, actor)


@router.get("/contacts", response_model=CRMListResponse)
async def list_contacts(
    session: SessionDependency,
    actor: CurrentUser,
    search: Search = None,
    company_id: UUID | None = None,
    contact_status: Annotated[
        ContactStatus | None, Query(alias="status")
    ] = None,
    owner_id: UUID | None = None,
    include_archived: bool = False,
    limit: Limit = 50,
    offset: Offset = 0,
    sort_by: CRMSortField = CRMSortField.CREATED_AT,
    direction: SortDirection = SortDirection.DESC,
) -> CRMListResponse:
    page = await CRMService(session).list_contacts(
        actor,
        ContactFilters(
            search, company_id, contact_status, owner_id, include_archived
        ),
        limit=limit,
        offset=offset,
        sort_by=sort_by,
        direction=direction,
    )
    return _page(page, ContactResponse)


@router.get("/contacts/{contact_id}", response_model=ContactResponse)
async def get_contact(
    contact_id: UUID, session: SessionDependency, actor: CurrentUser
) -> Contact:
    return await CRMService(session).get_contact(contact_id, actor)


@router.patch("/contacts/{contact_id}", response_model=ContactResponse)
async def update_contact(
    contact_id: UUID,
    data: ContactUpdate,
    session: SessionDependency,
    actor: CurrentUser,
) -> Contact:
    return await CRMService(session).update_contact(contact_id, data, actor)


@router.delete("/contacts/{contact_id}", status_code=204)
async def delete_contact(
    contact_id: UUID, session: SessionDependency, actor: CurrentUser
) -> Response:
    await CRMService(session).archive_contact(contact_id, actor)
    return Response(status_code=204)


@router.post("/leads", response_model=LeadResponse, status_code=201)
async def create_lead(
    data: LeadCreate, session: SessionDependency, actor: CurrentUser
) -> Lead:
    return await CRMService(session).create_lead(data, actor)


@router.get("/leads", response_model=CRMListResponse)
async def list_leads(
    session: SessionDependency,
    actor: CurrentUser,
    search: Search = None,
    lead_status: Annotated[LeadStatus | None, Query(alias="status")] = None,
    source: LeadSource | None = None,
    owner_id: UUID | None = None,
    company_id: UUID | None = None,
    contact_id: UUID | None = None,
    minimum_score: Annotated[int | None, Query(ge=0, le=100)] = None,
    follow_up_from: datetime | None = None,
    follow_up_to: datetime | None = None,
    include_archived: bool = False,
    limit: Limit = 50,
    offset: Offset = 0,
    sort_by: CRMSortField = CRMSortField.CREATED_AT,
    direction: SortDirection = SortDirection.DESC,
) -> CRMListResponse:
    page = await CRMService(session).list_leads(
        actor,
        LeadFilters(
            search,
            lead_status,
            source,
            owner_id,
            company_id,
            contact_id,
            minimum_score,
            follow_up_from,
            follow_up_to,
            include_archived,
        ),
        limit=limit,
        offset=offset,
        sort_by=sort_by,
        direction=direction,
    )
    return _page(page, LeadResponse)


@router.get("/leads/{lead_id}", response_model=LeadResponse)
async def get_lead(
    lead_id: UUID, session: SessionDependency, actor: CurrentUser
) -> Lead:
    return await CRMService(session).get_lead(lead_id, actor)


@router.patch("/leads/{lead_id}", response_model=LeadResponse)
async def update_lead(
    lead_id: UUID,
    data: LeadUpdate,
    session: SessionDependency,
    actor: CurrentUser,
) -> Lead:
    return await CRMService(session).update_lead(lead_id, data, actor)


@router.delete("/leads/{lead_id}", status_code=204)
async def delete_lead(
    lead_id: UUID, session: SessionDependency, actor: CurrentUser
) -> Response:
    await CRMService(session).archive_lead(lead_id, actor)
    return Response(status_code=204)


@router.post("/leads/{lead_id}/convert", response_model=DealResponse)
async def convert_lead(
    lead_id: UUID,
    data: LeadConvertRequest,
    session: SessionDependency,
    actor: CurrentUser,
) -> Deal:
    return await CRMService(session).convert_lead(lead_id, data, actor)


@router.post("/pipelines", response_model=PipelineResponse, status_code=201)
async def create_pipeline(
    data: PipelineCreate, session: SessionDependency, actor: CurrentUser
) -> Pipeline:
    return await CRMService(session).create_pipeline(data, actor)


@router.get("/pipelines", response_model=list[PipelineResponse])
async def list_pipelines(
    session: SessionDependency, actor: CurrentUser
) -> list[Pipeline]:
    return await CRMService(session).list_pipelines(actor)


@router.get("/pipelines/{pipeline_id}", response_model=PipelineResponse)
async def get_pipeline(
    pipeline_id: UUID, session: SessionDependency, actor: CurrentUser
) -> Pipeline:
    return await CRMService(session).get_pipeline(pipeline_id, actor)


@router.patch("/pipelines/{pipeline_id}", response_model=PipelineResponse)
async def update_pipeline(
    pipeline_id: UUID,
    data: PipelineUpdate,
    session: SessionDependency,
    actor: CurrentUser,
) -> Pipeline:
    return await CRMService(session).update_pipeline(pipeline_id, data, actor)


@router.delete("/pipelines/{pipeline_id}", status_code=204)
async def delete_pipeline(
    pipeline_id: UUID, session: SessionDependency, actor: CurrentUser
) -> Response:
    await CRMService(session).delete_pipeline(pipeline_id, actor)
    return Response(status_code=204)


@router.post(
    "/pipelines/{pipeline_id}/stages",
    response_model=PipelineStageResponse,
    status_code=201,
)
async def create_stage(
    pipeline_id: UUID,
    data: PipelineStageCreate,
    session: SessionDependency,
    actor: CurrentUser,
) -> PipelineStage:
    return await CRMService(session).create_stage(pipeline_id, data, actor)


@router.patch(
    "/pipelines/{pipeline_id}/stages/{stage_id}",
    response_model=PipelineStageResponse,
)
async def update_stage(
    pipeline_id: UUID,
    stage_id: UUID,
    data: PipelineStageUpdate,
    session: SessionDependency,
    actor: CurrentUser,
) -> PipelineStage:
    return await CRMService(session).update_stage(
        pipeline_id, stage_id, data, actor
    )


@router.delete(
    "/pipelines/{pipeline_id}/stages/{stage_id}", status_code=204
)
async def delete_stage(
    pipeline_id: UUID,
    stage_id: UUID,
    session: SessionDependency,
    actor: CurrentUser,
) -> Response:
    await CRMService(session).delete_stage(pipeline_id, stage_id, actor)
    return Response(status_code=204)


@router.post(
    "/pipelines/{pipeline_id}/stages/reorder",
    response_model=list[PipelineStageResponse],
)
async def reorder_stages(
    pipeline_id: UUID,
    data: StageReorderRequest,
    session: SessionDependency,
    actor: CurrentUser,
) -> list[PipelineStage]:
    return await CRMService(session).reorder_stages(pipeline_id, data, actor)


@router.post("/deals", response_model=DealResponse, status_code=201)
async def create_deal(
    data: DealCreate, session: SessionDependency, actor: CurrentUser
) -> Deal:
    return await CRMService(session).create_deal(data, actor)


@router.get("/deals", response_model=CRMListResponse)
async def list_deals(
    session: SessionDependency,
    actor: CurrentUser,
    search: Search = None,
    pipeline_id: UUID | None = None,
    stage_id: UUID | None = None,
    deal_status: Annotated[DealStatus | None, Query(alias="status")] = None,
    owner_id: UUID | None = None,
    company_id: UUID | None = None,
    primary_contact_id: UUID | None = None,
    close_from: datetime | None = None,
    close_to: datetime | None = None,
    minimum_value: Annotated[Decimal | None, Query(ge=0)] = None,
    maximum_value: Annotated[Decimal | None, Query(ge=0)] = None,
    include_archived: bool = False,
    limit: Limit = 50,
    offset: Offset = 0,
    sort_by: CRMSortField = CRMSortField.CREATED_AT,
    direction: SortDirection = SortDirection.DESC,
) -> CRMListResponse:
    page = await CRMService(session).list_deals(
        actor,
        DealFilters(
            search,
            pipeline_id,
            stage_id,
            deal_status,
            owner_id,
            company_id,
            primary_contact_id,
            close_from,
            close_to,
            minimum_value,
            maximum_value,
            include_archived,
        ),
        limit=limit,
        offset=offset,
        sort_by=sort_by,
        direction=direction,
    )
    return _page(page, DealResponse)


@router.get("/deals/{deal_id}", response_model=DealResponse)
async def get_deal(
    deal_id: UUID, session: SessionDependency, actor: CurrentUser
) -> Deal:
    return await CRMService(session).get_deal(deal_id, actor)


@router.patch("/deals/{deal_id}", response_model=DealResponse)
async def update_deal(
    deal_id: UUID,
    data: DealUpdate,
    session: SessionDependency,
    actor: CurrentUser,
) -> Deal:
    return await CRMService(session).update_deal(deal_id, data, actor)


@router.delete("/deals/{deal_id}", status_code=204)
async def delete_deal(
    deal_id: UUID, session: SessionDependency, actor: CurrentUser
) -> Response:
    await CRMService(session).archive_deal(deal_id, actor)
    return Response(status_code=204)


@router.patch("/deals/{deal_id}/stage", response_model=DealResponse)
async def move_deal_stage(
    deal_id: UUID,
    data: DealStageUpdate,
    session: SessionDependency,
    actor: CurrentUser,
) -> Deal:
    return await CRMService(session).move_deal_stage(deal_id, data, actor)


@router.post("/notes", response_model=CRMNoteResponse, status_code=201)
async def create_note(
    data: CRMNoteCreate, session: SessionDependency, actor: CurrentUser
) -> CRMNote:
    return await CRMService(session).create_note(data, actor)


@router.get("/notes", response_model=CRMListResponse)
async def list_notes(
    session: SessionDependency,
    actor: CurrentUser,
    company_id: UUID | None = None,
    contact_id: UUID | None = None,
    lead_id: UUID | None = None,
    deal_id: UUID | None = None,
    author_id: UUID | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> CRMListResponse:
    page = await CRMService(session).list_notes(
        actor,
        NoteFilters(
            company_id,
            contact_id,
            lead_id,
            deal_id,
            author_id,
            start_date,
            end_date,
        ),
        limit=limit,
        offset=offset,
    )
    return _page(page, CRMNoteResponse)


@router.patch("/notes/{note_id}", response_model=CRMNoteResponse)
async def update_note(
    note_id: UUID,
    data: CRMNoteUpdate,
    session: SessionDependency,
    actor: CurrentUser,
) -> CRMNote:
    return await CRMService(session).update_note(note_id, data, actor)


@router.delete("/notes/{note_id}", status_code=204)
async def delete_note(
    note_id: UUID, session: SessionDependency, actor: CurrentUser
) -> Response:
    await CRMService(session).delete_note(note_id, actor)
    return Response(status_code=204)


@router.post("/activities", response_model=CRMActivityResponse, status_code=201)
async def create_activity(
    data: CRMActivityCreate,
    session: SessionDependency,
    actor: CurrentUser,
) -> CRMActivity:
    return await CRMService(session).create_activity(data, actor)


@router.get("/activities", response_model=CRMListResponse)
async def list_activities(
    session: SessionDependency,
    actor: CurrentUser,
    activity_type: Annotated[
        CRMActivityType | None, Query(alias="type")
    ] = None,
    actor_id: UUID | None = None,
    assigned_to: UUID | None = None,
    company_id: UUID | None = None,
    contact_id: UUID | None = None,
    lead_id: UUID | None = None,
    deal_id: UUID | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    completed: bool | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
    direction: SortDirection = SortDirection.DESC,
) -> CRMListResponse:
    page = await CRMService(session).list_activities(
        actor,
        CRMActivityFilters(
            activity_type,
            actor_id,
            assigned_to,
            company_id,
            contact_id,
            lead_id,
            deal_id,
            start_date,
            end_date,
            completed,
        ),
        limit=limit,
        offset=offset,
        direction=direction,
    )
    return _page(page, CRMActivityResponse)


@router.get("/activities/{activity_id}", response_model=CRMActivityResponse)
async def get_activity(
    activity_id: UUID, session: SessionDependency, actor: CurrentUser
) -> CRMActivity:
    return await CRMService(session).get_activity(activity_id, actor)


@router.patch("/activities/{activity_id}", response_model=CRMActivityResponse)
async def update_activity(
    activity_id: UUID,
    data: CRMActivityUpdate,
    session: SessionDependency,
    actor: CurrentUser,
) -> CRMActivity:
    return await CRMService(session).update_activity(
        activity_id, data, actor
    )


@router.post(
    "/activities/{activity_id}/complete",
    response_model=CRMActivityResponse,
)
async def complete_activity(
    activity_id: UUID,
    data: CRMActivityComplete,
    session: SessionDependency,
    actor: CurrentUser,
) -> CRMActivity:
    return await CRMService(session).complete_activity(
        activity_id, data, actor
    )


@router.delete("/activities/{activity_id}", status_code=204)
async def delete_activity(
    activity_id: UUID, session: SessionDependency, actor: CurrentUser
) -> Response:
    await CRMService(session).delete_activity(activity_id, actor)
    return Response(status_code=204)


def _page(page: object, schema: type[BaseModel]) -> CRMListResponse:
    items = getattr(page, "items")
    return CRMListResponse(
        items=cast(Any, [schema.model_validate(item) for item in items]),
        total=getattr(page, "total"),
        limit=getattr(page, "limit"),
        offset=getattr(page, "offset"),
    )
