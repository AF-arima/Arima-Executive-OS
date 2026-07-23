from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import TypeVar
from urllib.parse import urlparse
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)

from app.database.models import (
    CRMActivityType,
    CompanyStatus,
    ContactStatus,
    DealStatus,
    LeadSource,
    LeadStatus,
)
from app.schemas.auth import StrictSchema

SchemaT = TypeVar("SchemaT", bound=BaseModel)


class CRMSortField(str, Enum):
    CREATED_AT = "created_at"
    UPDATED_AT = "updated_at"
    NAME = "name"
    TITLE = "title"
    VALUE = "value"
    EXPECTED_CLOSE_DATE = "expected_close_date"
    DUE_AT = "due_at"


def _nonblank(value: str, label: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} cannot be blank")
    return normalized


def _aware(value: datetime | None, label: str) -> datetime | None:
    if value is not None and (
        value.tzinfo is None or value.utcoffset() is None
    ):
        raise ValueError(f"{label} must include a timezone")
    return value


def normalize_domain(value: str | None) -> str | None:
    if value is None:
        return None
    candidate = value.strip().lower().rstrip(".")
    if not candidate:
        return None
    parsed = urlparse(
        candidate if "://" in candidate else f"//{candidate}"
    )
    domain = (parsed.hostname or "").lower().rstrip(".")
    if (
        not domain
        or len(domain) > 253
        or "." not in domain
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("domain must be a valid hostname")
    return domain


def normalize_website(value: str | None) -> str | None:
    if value is None:
        return None
    candidate = value.strip()
    if not candidate:
        return None
    parsed = urlparse(
        candidate if "://" in candidate else f"https://{candidate}"
    )
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("website must be a public HTTP(S) URL")
    host = parsed.hostname.lower()
    port = f":{parsed.port}" if parsed.port is not None else ""
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme.lower()}://{host}{port}{path}"


class CompanyCreate(StrictSchema):
    name: str = Field(min_length=1, max_length=255)
    legal_name: str | None = Field(default=None, max_length=255)
    website: str | None = Field(default=None, max_length=500)
    domain: str | None = Field(default=None, max_length=253)
    industry: str | None = Field(default=None, max_length=100)
    company_size: str | None = Field(default=None, max_length=50)
    country: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)
    address: str | None = Field(default=None, max_length=500)
    description: str | None = Field(default=None, max_length=5000)
    status: CompanyStatus = CompanyStatus.PROSPECT
    owner_id: UUID | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _nonblank(value, "Company name")

    @field_validator("domain")
    @classmethod
    def validate_domain(cls, value: str | None) -> str | None:
        return normalize_domain(value)

    @field_validator("website")
    @classmethod
    def validate_website(cls, value: str | None) -> str | None:
        return normalize_website(value)


class CompanyUpdate(StrictSchema):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    legal_name: str | None = Field(default=None, max_length=255)
    website: str | None = Field(default=None, max_length=500)
    domain: str | None = Field(default=None, max_length=253)
    industry: str | None = Field(default=None, max_length=100)
    company_size: str | None = Field(default=None, max_length=50)
    country: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)
    address: str | None = Field(default=None, max_length=500)
    description: str | None = Field(default=None, max_length=5000)
    status: CompanyStatus | None = None
    owner_id: UUID | None = None

    @field_validator("name")
    @classmethod
    def validate_optional_name(cls, value: str | None) -> str | None:
        return None if value is None else _nonblank(value, "Company name")

    @field_validator("domain")
    @classmethod
    def validate_domain(cls, value: str | None) -> str | None:
        return normalize_domain(value)

    @field_validator("website")
    @classmethod
    def validate_website(cls, value: str | None) -> str | None:
        return normalize_website(value)

    @model_validator(mode="after")
    def reject_null_required(self) -> "CompanyUpdate":
        return _reject_null(self, ("name", "status"))


class CompanyResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    name: str
    legal_name: str | None
    website: str | None
    domain: str | None
    industry: str | None
    company_size: str | None
    country: str | None
    city: str | None
    address: str | None
    description: str | None
    status: CompanyStatus
    owner_id: UUID | None
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None


class ContactCreate(StrictSchema):
    company_id: UUID | None = None
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    job_title: str | None = Field(default=None, max_length=150)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=50)
    linkedin_url: str | None = Field(default=None, max_length=500)
    country: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)
    status: ContactStatus = ContactStatus.PROSPECT
    owner_id: UUID | None = None

    @field_validator("first_name", "last_name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _nonblank(value, "Contact name")

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr | None) -> str | None:
        return None if value is None else str(value).lower()


class ContactUpdate(StrictSchema):
    company_id: UUID | None = None
    first_name: str | None = Field(default=None, min_length=1, max_length=100)
    last_name: str | None = Field(default=None, min_length=1, max_length=100)
    job_title: str | None = Field(default=None, max_length=150)
    email: EmailStr | None = None
    phone: str | None = Field(default=None, max_length=50)
    linkedin_url: str | None = Field(default=None, max_length=500)
    country: str | None = Field(default=None, max_length=100)
    city: str | None = Field(default=None, max_length=100)
    status: ContactStatus | None = None
    owner_id: UUID | None = None

    @field_validator("first_name", "last_name")
    @classmethod
    def validate_optional_name(cls, value: str | None) -> str | None:
        return None if value is None else _nonblank(value, "Contact name")

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr | None) -> str | None:
        return None if value is None else str(value).lower()

    @model_validator(mode="after")
    def reject_null_required(self) -> "ContactUpdate":
        return _reject_null(self, ("first_name", "last_name", "status"))


class ContactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    company_id: UUID | None
    first_name: str
    last_name: str
    job_title: str | None
    email: EmailStr | None
    phone: str | None
    linkedin_url: str | None
    country: str | None
    city: str | None
    status: ContactStatus
    owner_id: UUID | None
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None


class LeadCreate(StrictSchema):
    company_id: UUID | None = None
    contact_id: UUID | None = None
    title: str = Field(min_length=1, max_length=255)
    source: LeadSource
    status: LeadStatus = LeadStatus.NEW
    score: int | None = Field(default=None, ge=0, le=100)
    estimated_value: Decimal | None = Field(
        default=None, ge=0, max_digits=18, decimal_places=2
    )
    currency: str = Field(default="GBP", pattern=r"^[A-Z]{3}$")
    owner_id: UUID | None = None
    last_contacted_at: datetime | None = None
    next_follow_up_at: datetime | None = None
    loss_reason: str | None = Field(default=None, max_length=1000)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return _nonblank(value, "Lead title")

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("last_contacted_at", "next_follow_up_at")
    @classmethod
    def validate_dates(cls, value: datetime | None) -> datetime | None:
        return _aware(value, "CRM datetime")


class LeadUpdate(StrictSchema):
    company_id: UUID | None = None
    contact_id: UUID | None = None
    title: str | None = Field(default=None, min_length=1, max_length=255)
    source: LeadSource | None = None
    status: LeadStatus | None = None
    score: int | None = Field(default=None, ge=0, le=100)
    estimated_value: Decimal | None = Field(
        default=None, ge=0, max_digits=18, decimal_places=2
    )
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    owner_id: UUID | None = None
    last_contacted_at: datetime | None = None
    next_follow_up_at: datetime | None = None
    loss_reason: str | None = Field(default=None, max_length=1000)

    @field_validator("title")
    @classmethod
    def validate_optional_title(cls, value: str | None) -> str | None:
        return None if value is None else _nonblank(value, "Lead title")

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str | None) -> str | None:
        return None if value is None else value.strip().upper()

    @field_validator("last_contacted_at", "next_follow_up_at")
    @classmethod
    def validate_dates(cls, value: datetime | None) -> datetime | None:
        return _aware(value, "CRM datetime")

    @model_validator(mode="after")
    def reject_null_required(self) -> "LeadUpdate":
        return _reject_null(
            self, ("title", "source", "status", "currency")
        )


class LeadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    company_id: UUID | None
    contact_id: UUID | None
    title: str
    source: LeadSource
    status: LeadStatus
    score: int | None
    estimated_value: Decimal | None
    currency: str
    owner_id: UUID | None
    created_by: UUID
    last_contacted_at: datetime | None
    next_follow_up_at: datetime | None
    qualified_at: datetime | None
    converted_at: datetime | None
    lost_at: datetime | None
    loss_reason: str | None
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None


class PipelineStageCreate(StrictSchema):
    name: str = Field(min_length=1, max_length=100)
    position: int = Field(ge=0)
    probability: int = Field(ge=0, le=100)
    is_closed: bool = False
    is_won: bool = False

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _nonblank(value, "Stage name")

    @model_validator(mode="after")
    def validate_won(self) -> "PipelineStageCreate":
        if self.is_won and not self.is_closed:
            raise ValueError("A won stage must be closed")
        return self


class PipelineStageUpdate(StrictSchema):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    position: int | None = Field(default=None, ge=0)
    probability: int | None = Field(default=None, ge=0, le=100)
    is_closed: bool | None = None
    is_won: bool | None = None

    @model_validator(mode="after")
    def reject_null_required(self) -> "PipelineStageUpdate":
        return _reject_null(
            self,
            ("name", "position", "probability", "is_closed", "is_won"),
        )


class PipelineStageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    pipeline_id: UUID
    name: str
    position: int
    probability: int
    is_closed: bool
    is_won: bool
    created_at: datetime
    updated_at: datetime


class PipelineCreate(StrictSchema):
    name: str = Field(min_length=1, max_length=150)
    description: str | None = Field(default=None, max_length=1000)
    is_default: bool = False
    is_active: bool = True

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        return _nonblank(value, "Pipeline name")


class PipelineUpdate(StrictSchema):
    name: str | None = Field(default=None, min_length=1, max_length=150)
    description: str | None = Field(default=None, max_length=1000)
    is_default: bool | None = None
    is_active: bool | None = None

    @model_validator(mode="after")
    def reject_null_required(self) -> "PipelineUpdate":
        return _reject_null(
            self, ("name", "is_default", "is_active")
        )


class PipelineResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    name: str
    description: str | None
    is_default: bool
    is_active: bool
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    stages: list[PipelineStageResponse] = []


class StageOrder(StrictSchema):
    stage_id: UUID
    position: int = Field(ge=0)


class StageReorderRequest(StrictSchema):
    stages: list[StageOrder] = Field(min_length=1, max_length=100)


class DealCreate(StrictSchema):
    pipeline_id: UUID
    stage_id: UUID
    company_id: UUID | None = None
    primary_contact_id: UUID | None = None
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=5000)
    value: Decimal = Field(ge=0, max_digits=18, decimal_places=2)
    currency: str = Field(default="GBP", pattern=r"^[A-Z]{3}$")
    probability: int | None = Field(default=None, ge=0, le=100)
    expected_close_date: datetime | None = None
    owner_id: UUID | None = None
    lost_reason: str | None = Field(default=None, max_length=1000)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str) -> str:
        return _nonblank(value, "Deal title")

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("expected_close_date")
    @classmethod
    def validate_date(cls, value: datetime | None) -> datetime | None:
        return _aware(value, "expected_close_date")


class DealUpdate(StrictSchema):
    pipeline_id: UUID | None = None
    stage_id: UUID | None = None
    company_id: UUID | None = None
    primary_contact_id: UUID | None = None
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=5000)
    value: Decimal | None = Field(
        default=None, ge=0, max_digits=18, decimal_places=2
    )
    currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")
    probability: int | None = Field(default=None, ge=0, le=100)
    expected_close_date: datetime | None = None
    owner_id: UUID | None = None
    lost_reason: str | None = Field(default=None, max_length=1000)

    @field_validator("title")
    @classmethod
    def validate_title(cls, value: str | None) -> str | None:
        return None if value is None else _nonblank(value, "Deal title")

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str | None) -> str | None:
        return None if value is None else value.strip().upper()

    @field_validator("expected_close_date")
    @classmethod
    def validate_date(cls, value: datetime | None) -> datetime | None:
        return _aware(value, "expected_close_date")

    @model_validator(mode="after")
    def reject_null_required(self) -> "DealUpdate":
        return _reject_null(
            self,
            ("pipeline_id", "stage_id", "title", "value", "currency"),
        )


class DealStageUpdate(StrictSchema):
    stage_id: UUID
    probability: int | None = Field(default=None, ge=0, le=100)
    lost_reason: str | None = Field(default=None, max_length=1000)


class DealResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    pipeline_id: UUID
    stage_id: UUID
    company_id: UUID | None
    primary_contact_id: UUID | None
    originating_lead_id: UUID | None
    title: str
    description: str | None
    value: Decimal
    currency: str
    probability: int
    expected_close_date: datetime | None
    actual_close_date: datetime | None
    owner_id: UUID
    created_by: UUID
    status: DealStatus
    lost_reason: str | None
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None


class LeadConvertRequest(StrictSchema):
    pipeline_id: UUID | None = None
    stage_id: UUID | None = None
    company_id: UUID | None = None
    contact_id: UUID | None = None
    deal_title: str | None = Field(default=None, min_length=1, max_length=255)
    value: Decimal | None = Field(
        default=None, ge=0, max_digits=18, decimal_places=2
    )
    expected_close_date: datetime | None = None

    @field_validator("expected_close_date")
    @classmethod
    def validate_date(cls, value: datetime | None) -> datetime | None:
        return _aware(value, "expected_close_date")


class CRMNoteCreate(StrictSchema):
    company_id: UUID | None = None
    contact_id: UUID | None = None
    lead_id: UUID | None = None
    deal_id: UUID | None = None
    body: str = Field(min_length=1, max_length=10000)

    @field_validator("body")
    @classmethod
    def validate_body(cls, value: str) -> str:
        return _nonblank(value, "Note body")

    @model_validator(mode="after")
    def validate_parent(self) -> "CRMNoteCreate":
        parents = (self.company_id, self.contact_id, self.lead_id, self.deal_id)
        if sum(item is not None for item in parents) != 1:
            raise ValueError("Exactly one note parent is required")
        return self


class CRMNoteUpdate(StrictSchema):
    body: str = Field(min_length=1, max_length=10000)

    @field_validator("body")
    @classmethod
    def validate_body(cls, value: str) -> str:
        return _nonblank(value, "Note body")


class CRMNoteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    author_id: UUID
    company_id: UUID | None
    contact_id: UUID | None
    lead_id: UUID | None
    deal_id: UUID | None
    body: str
    created_at: datetime
    updated_at: datetime


class CRMActivityCreate(StrictSchema):
    type: CRMActivityType
    subject: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=5000)
    company_id: UUID | None = None
    contact_id: UUID | None = None
    lead_id: UUID | None = None
    deal_id: UUID | None = None
    assigned_to: UUID | None = None
    occurred_at: datetime | None = None
    due_at: datetime | None = None
    completed_at: datetime | None = None
    outcome: str | None = Field(default=None, max_length=1000)

    @field_validator("subject")
    @classmethod
    def validate_subject(cls, value: str) -> str:
        return _nonblank(value, "Activity subject")

    @field_validator("occurred_at", "due_at", "completed_at")
    @classmethod
    def validate_dates(cls, value: datetime | None) -> datetime | None:
        return _aware(value, "Activity datetime")

    @model_validator(mode="after")
    def validate_parent(self) -> "CRMActivityCreate":
        parents = (self.company_id, self.contact_id, self.lead_id, self.deal_id)
        if not any(item is not None for item in parents):
            raise ValueError("At least one CRM reference is required")
        return self


class CRMActivityUpdate(StrictSchema):
    type: CRMActivityType | None = None
    subject: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=5000)
    company_id: UUID | None = None
    contact_id: UUID | None = None
    lead_id: UUID | None = None
    deal_id: UUID | None = None
    assigned_to: UUID | None = None
    occurred_at: datetime | None = None
    due_at: datetime | None = None
    completed_at: datetime | None = None
    outcome: str | None = Field(default=None, max_length=1000)

    @field_validator("subject")
    @classmethod
    def validate_subject(cls, value: str | None) -> str | None:
        return None if value is None else _nonblank(value, "Activity subject")

    @field_validator("occurred_at", "due_at", "completed_at")
    @classmethod
    def validate_dates(cls, value: datetime | None) -> datetime | None:
        return _aware(value, "Activity datetime")

    @model_validator(mode="after")
    def reject_null_required(self) -> "CRMActivityUpdate":
        return _reject_null(self, ("type", "subject"))


class CRMActivityComplete(StrictSchema):
    outcome: str | None = Field(default=None, max_length=1000)
    occurred_at: datetime | None = None

    @field_validator("occurred_at")
    @classmethod
    def validate_date(cls, value: datetime | None) -> datetime | None:
        return _aware(value, "occurred_at")


class CRMActivityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    type: CRMActivityType
    subject: str
    description: str | None
    company_id: UUID | None
    contact_id: UUID | None
    lead_id: UUID | None
    deal_id: UUID | None
    actor_id: UUID
    assigned_to: UUID | None
    occurred_at: datetime | None
    due_at: datetime | None
    completed_at: datetime | None
    outcome: str | None
    created_at: datetime
    updated_at: datetime


class CRMListResponse(BaseModel):
    items: list[
        CompanyResponse
        | ContactResponse
        | LeadResponse
        | DealResponse
        | CRMNoteResponse
        | CRMActivityResponse
    ]
    total: int = Field(ge=0)
    limit: int = Field(ge=1)
    offset: int = Field(ge=0)


class CRMTimeSeriesPoint(BaseModel):
    period_start: datetime
    value: Decimal = Field(ge=0)


class CRMPipelineAnalytics(BaseModel):
    open_pipeline_value: Decimal = Field(ge=0)
    weighted_pipeline_value: Decimal = Field(ge=0)
    deal_count_by_stage: dict[str, int]
    value_by_stage: dict[str, Decimal]
    win_count: int = Field(ge=0)
    loss_count: int = Field(ge=0)
    win_rate: float = Field(ge=0, le=1)
    average_won_deal_size: Decimal = Field(ge=0)
    expected_closes: list[CRMTimeSeriesPoint]
    generated_at: datetime


class CRMLeadAnalytics(BaseModel):
    leads_by_status: dict[LeadStatus, int]
    leads_by_source: dict[LeadSource, int]
    qualification_rate: float = Field(ge=0, le=1)
    conversion_rate: float = Field(ge=0, le=1)
    average_lead_score: float = Field(ge=0, le=100)
    average_time_to_qualification_hours: float = Field(ge=0)
    average_time_to_conversion_hours: float = Field(ge=0)
    lost_total: int = Field(ge=0)
    disqualified_total: int = Field(ge=0)
    generated_at: datetime


class CRMActivityAnalytics(BaseModel):
    scheduled: int = Field(ge=0)
    completed: int = Field(ge=0)
    overdue: int = Field(ge=0)
    activity_count_by_type: dict[CRMActivityType, int]
    completion_rate: float = Field(ge=0, le=1)
    timeline: list[CRMTimeSeriesPoint]
    generated_at: datetime


def _reject_null(model: SchemaT, fields: tuple[str, ...]) -> SchemaT:
    for field_name in fields:
        if (
            field_name in model.model_fields_set
            and getattr(model, field_name) is None
        ):
            raise ValueError(f"{field_name} cannot be null")
    return model
