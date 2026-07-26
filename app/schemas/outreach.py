from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.database.models import (
    ApprovalStatus,
    AutomationAction,
    AutomationTrigger,
    DeliveryEventType,
    DraftStatus,
    MailboxProvider,
    OutreachStatus,
    QueueStatus,
)
from app.schemas.auth import StrictSchema


def nonblank(value: str) -> str:
    result = value.strip()
    if not result:
        raise ValueError("Value cannot be blank")
    return result


def aware(value: datetime | None) -> datetime | None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError("Datetime must include a timezone")
    return value


class MailboxCreate(StrictSchema):
    provider: MailboxProvider
    email_address: EmailStr
    display_name: str | None = Field(default=None, max_length=200)
    credential_reference: str = Field(min_length=1, max_length=500)
    signature_html: str | None = Field(default=None, max_length=20000)
    daily_send_limit: int = Field(default=100, ge=1, le=10000)

    @field_validator("email_address")
    @classmethod
    def lower_email(cls, value: EmailStr) -> str:
        return str(value).lower()

    @field_validator("credential_reference")
    @classmethod
    def safe_reference(cls, value: str) -> str:
        result = nonblank(value)
        if not result.lower().startswith(
            (
                "vault://",
                "aws-secrets://",
                "gcp-secrets://",
                "azure-keyvault://",
            )
        ):
            raise ValueError("Use a supported external credential reference")
        return result


class MailboxResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    provider: MailboxProvider
    email_address: EmailStr
    display_name: str | None
    signature_html: str | None
    is_active: bool
    daily_send_limit: int
    owner_id: UUID
    created_at: datetime
    updated_at: datetime


class TemplateCreate(StrictSchema):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)
    subject: str = Field(min_length=1, max_length=500)
    body_html: str = Field(min_length=1, max_length=100000)
    body_text: str | None = Field(default=None, max_length=100000)
    variables: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("name", "subject", "body_html")
    @classmethod
    def strip_required(cls, value: str) -> str:
        return nonblank(value)


class TemplateVersionCreate(StrictSchema):
    subject: str = Field(min_length=1, max_length=500)
    body_html: str = Field(min_length=1, max_length=100000)
    body_text: str | None = Field(default=None, max_length=100000)
    variables: list[str] = Field(default_factory=list, max_length=100)


class TemplateVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    template_id: UUID
    version: int
    subject: str
    body_html: str
    body_text: str | None
    variables: list[str]
    created_at: datetime


class TemplateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    description: str | None
    owner_id: UUID
    is_archived: bool
    created_at: datetime
    updated_at: datetime
    versions: list[TemplateVersionResponse]


class AttachmentCreate(StrictSchema):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=200)
    size_bytes: int = Field(ge=0, le=26214400)
    storage_key: str = Field(min_length=1, max_length=1000)
    checksum_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class AttachmentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    filename: str
    content_type: str
    size_bytes: int
    storage_key: str
    checksum_sha256: str


class DraftCreate(StrictSchema):
    mailbox_id: UUID
    template_version_id: UUID | None = None
    contact_id: UUID | None = None
    to_email: EmailStr
    cc: list[EmailStr] = Field(default_factory=list, max_length=50)
    bcc: list[EmailStr] = Field(default_factory=list, max_length=50)
    subject: str = Field(min_length=1, max_length=500)
    body_html: str = Field(min_length=1, max_length=100000)
    body_text: str | None = Field(default=None, max_length=100000)
    variable_values: dict[str, str] = Field(default_factory=dict)
    scheduled_at: datetime | None = None
    attachments: list[AttachmentCreate] = Field(default_factory=list, max_length=20)

    @field_validator("scheduled_at")
    @classmethod
    def validate_time(cls, value: datetime | None) -> datetime | None:
        return aware(value)


class DraftResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    mailbox_id: UUID
    template_version_id: UUID | None
    contact_id: UUID | None
    to_email: EmailStr
    cc: list[str]
    bcc: list[str]
    subject: str
    body_html: str
    body_text: str | None
    variable_values: dict[str, str]
    status: DraftStatus
    scheduled_at: datetime | None
    owner_id: UUID
    created_at: datetime
    updated_at: datetime
    attachments: list[AttachmentResponse]


class ScheduleRequest(StrictSchema):
    scheduled_at: datetime

    @field_validator("scheduled_at")
    @classmethod
    def validate_time(cls, value: datetime) -> datetime:
        result = aware(value)
        assert result is not None
        return result


class ApprovalRequest(StrictSchema):
    reviewer_id: UUID


class SequenceCreate(StrictSchema):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=1000)


class SequenceStepCreate(StrictSchema):
    position: int = Field(ge=0)
    delay_minutes: int = Field(default=0, ge=0, le=525600)
    template_version_id: UUID
    requires_approval: bool = False


class SequenceStepResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    sequence_id: UUID
    position: int
    delay_minutes: int
    template_version_id: UUID
    requires_approval: bool


class SequenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    description: str | None
    status: OutreachStatus
    owner_id: UUID
    created_at: datetime
    updated_at: datetime
    steps: list[SequenceStepResponse]


class AudienceCreate(StrictSchema):
    name: str = Field(min_length=1, max_length=200)
    filter_definition: dict[str, object] = Field(default_factory=dict)


class AudienceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    filter_definition: dict[str, object]
    owner_id: UUID
    created_at: datetime
    updated_at: datetime


class CampaignCreate(StrictSchema):
    name: str = Field(min_length=1, max_length=200)
    sequence_id: UUID
    audience_id: UUID
    mailbox_id: UUID
    scheduled_at: datetime | None = None

    @field_validator("scheduled_at")
    @classmethod
    def validate_time(cls, value: datetime | None) -> datetime | None:
        return aware(value)


class CampaignResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    sequence_id: UUID
    audience_id: UUID
    mailbox_id: UUID
    status: OutreachStatus
    scheduled_at: datetime | None
    owner_id: UUID
    created_at: datetime
    updated_at: datetime


class EnrollRequest(StrictSchema):
    contact_ids: list[UUID] = Field(min_length=1, max_length=1000)
    campaign_id: UUID


class ApprovalDecision(StrictSchema):
    approved: bool
    reason: str | None = Field(default=None, max_length=1000)


class ApprovalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    draft_id: UUID
    requested_by: UUID
    reviewer_id: UUID | None
    status: ApprovalStatus
    decided_at: datetime | None
    decision_reason: str | None


class DeliveryEventCreate(StrictSchema):
    queue_item_id: UUID
    type: DeliveryEventType
    occurred_at: datetime
    provider_event_id: str = Field(min_length=1, max_length=500)
    safe_metadata: dict[str, object] = Field(default_factory=dict)

    @field_validator("occurred_at")
    @classmethod
    def validate_time(cls, value: datetime) -> datetime:
        result = aware(value)
        assert result is not None
        return result

    @field_validator("safe_metadata")
    @classmethod
    def allow_safe_metadata(cls, value: dict[str, object]) -> dict[str, object]:
        allowed = {
            "link_id",
            "smtp_code",
            "bounce_category",
            "user_agent_class",
        }
        if not set(value).issubset(allowed):
            raise ValueError("Unsupported delivery event metadata")
        if any(
            not isinstance(item, (str, int, float, bool, type(None)))
            for item in value.values()
        ):
            raise ValueError("Delivery event metadata must be scalar")
        return value


class AutomationCreate(StrictSchema):
    name: str = Field(min_length=1, max_length=200)
    trigger: AutomationTrigger
    action: AutomationAction
    conditions: dict[str, object] = Field(default_factory=dict)
    action_config: dict[str, object] = Field(default_factory=dict)


class AutomationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    trigger: AutomationTrigger
    action: AutomationAction
    conditions: dict[str, object]
    action_config: dict[str, object]
    is_active: bool
    owner_id: UUID


class OutreachList(BaseModel):
    items: list[dict[str, object]]
    total: int
    limit: int
    offset: int


class OutreachAnalytics(BaseModel):
    drafts_by_status: dict[DraftStatus, int]
    queue_by_status: dict[QueueStatus, int]
    events_by_type: dict[DeliveryEventType, int]
    sent: int
    delivered: int
    opened: int
    clicked: int
    replied: int
    bounced: int
    unsubscribed: int
    delivery_rate: float = Field(ge=0, le=1)
    open_rate: float = Field(ge=0, le=1)
    click_rate: float = Field(ge=0, le=1)
    reply_rate: float = Field(ge=0, le=1)
    generated_at: datetime
