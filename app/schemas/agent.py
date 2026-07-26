from datetime import datetime
from decimal import Decimal
import re
from typing import Generic, TypeVar, overload
from uuid import UUID

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
)

from app.database.models.agent import (
    AgentApprovalStatus,
    AgentAttachmentStatus,
    AgentMemoryScope,
    AgentMemoryType,
    AgentRiskLevel,
    AgentRunStatus,
    AgentStatus,
    ConversationPriority,
    ConversationStatus,
    MessageContentType,
    MessageRole,
    ToolExecutionMode,
    ToolExecutionStatus,
)
from app.schemas.auth import StrictSchema

SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:[.-][a-z0-9]+)*$")
MEMORY_KEY_PATTERN = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")


@overload
def normalize_slug(value: str) -> str: ...


@overload
def normalize_slug(value: None) -> None: ...


def normalize_slug(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if not SLUG_PATTERN.fullmatch(normalized):
        raise ValueError(
            "slug must contain lowercase letters, numbers, dots, or hyphens"
        )
    return normalized


@overload
def normalize_memory_key(value: str) -> str: ...


@overload
def normalize_memory_key(value: None) -> None: ...


def normalize_memory_key(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if not MEMORY_KEY_PATTERN.fullmatch(normalized):
        raise ValueError(
            "key must contain lowercase letters, numbers, dots, "
            "underscores, or hyphens"
        )
    return normalized


def require_aware(value: datetime | None) -> datetime | None:
    if value is not None and (
        value.tzinfo is None or value.utcoffset() is None
    ):
        raise ValueError("datetime must include a timezone")
    return value


class AgentReadSchema(BaseModel):
    model_config = ConfigDict(
        from_attributes=True,
        extra="forbid",
        populate_by_name=True,
    )


ReadT = TypeVar("ReadT")


class AgentList(BaseModel, Generic[ReadT]):
    model_config = ConfigDict(extra="forbid")

    items: list[ReadT]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)


class AgentFilter(StrictSchema):
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class AgentDefinitionCreate(StrictSchema):
    slug: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    system_instructions: str = Field(min_length=1, max_length=100000)
    status: AgentStatus = AgentStatus.DRAFT
    version: int = Field(default=1, ge=1)
    is_default: bool = False
    created_by_id: UUID

    _slug = field_validator("slug")(normalize_slug)


class AgentDefinitionUpdate(StrictSchema):
    slug: str | None = Field(default=None, min_length=1, max_length=120)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    system_instructions: str | None = Field(
        default=None, min_length=1, max_length=100000
    )
    status: AgentStatus | None = None
    version: int | None = Field(default=None, ge=1)
    is_default: bool | None = None

    _slug = field_validator("slug")(normalize_slug)


class AgentDefinitionRead(AgentReadSchema):
    id: UUID
    slug: str
    name: str
    description: str | None
    system_instructions: str
    status: AgentStatus
    version: int
    is_default: bool
    created_by_id: UUID
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None


class AgentDefinitionFilter(AgentFilter):
    status: AgentStatus | None = None
    is_default: bool | None = None
    include_archived: bool = False


class AgentDefinitionList(AgentList[AgentDefinitionRead]):
    pass


class AgentConversationCreate(StrictSchema):
    agent_id: UUID
    owner_id: UUID
    title: str = Field(min_length=1, max_length=300)
    status: ConversationStatus = ConversationStatus.ACTIVE
    priority: ConversationPriority = ConversationPriority.NORMAL
    pinned: bool = False
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    last_message_at: datetime | None = None

    _last_message_at = field_validator("last_message_at")(require_aware)


class AgentConversationUpdate(StrictSchema):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    status: ConversationStatus | None = None
    priority: ConversationPriority | None = None
    pinned: bool | None = None
    metadata: dict[str, JsonValue] | None = None
    last_message_at: datetime | None = None

    _last_message_at = field_validator("last_message_at")(require_aware)


class AgentConversationRead(AgentReadSchema):
    id: UUID
    agent_id: UUID
    owner_id: UUID
    title: str
    status: ConversationStatus
    priority: ConversationPriority
    pinned: bool
    metadata: dict[str, JsonValue] = Field(
        validation_alias=AliasChoices("metadata", "metadata_")
    )
    last_message_at: datetime | None
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None


class AgentConversationFilter(AgentFilter):
    owner_id: UUID | None = None
    agent_id: UUID | None = None
    status: ConversationStatus | None = None
    priority: ConversationPriority | None = None
    pinned: bool | None = None
    include_archived: bool = False


class AgentConversationList(AgentList[AgentConversationRead]):
    pass


class AgentMessageCreate(StrictSchema):
    conversation_id: UUID
    run_id: UUID | None = None
    parent_message_id: UUID | None = None
    role: MessageRole
    content: str = Field(min_length=1, max_length=500000)
    content_type: MessageContentType = MessageContentType.TEXT
    sequence_number: int = Field(ge=1)
    token_count: int | None = Field(default=None, ge=0)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    created_by_id: UUID | None = None


class AgentMessageUpdate(StrictSchema):
    content: str | None = Field(default=None, min_length=1, max_length=500000)
    content_type: MessageContentType | None = None
    token_count: int | None = Field(default=None, ge=0)
    metadata: dict[str, JsonValue] | None = None


class AgentMessageRead(AgentReadSchema):
    id: UUID
    conversation_id: UUID
    run_id: UUID | None
    parent_message_id: UUID | None
    role: MessageRole
    content: str
    content_type: MessageContentType
    sequence_number: int
    token_count: int | None
    metadata: dict[str, JsonValue] = Field(
        validation_alias=AliasChoices("metadata", "metadata_")
    )
    created_by_id: UUID | None
    created_at: datetime


class AgentMessageFilter(AgentFilter):
    conversation_id: UUID | None = None
    run_id: UUID | None = None
    role: MessageRole | None = None


class AgentMessageList(AgentList[AgentMessageRead]):
    pass


class AgentRunCreate(StrictSchema):
    conversation_id: UUID
    agent_id: UUID
    triggered_by_id: UUID
    status: AgentRunStatus = AgentRunStatus.QUEUED
    input_message_id: UUID | None = None
    output_message_id: UUID | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    failure_code: str | None = Field(default=None, max_length=100)
    failure_message: str | None = Field(default=None, max_length=2000)
    model_provider: str | None = Field(default=None, max_length=100)
    model_name: str | None = Field(default=None, max_length=200)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    estimated_cost_gbp: Decimal | None = Field(default=None, ge=0)
    latency_ms: int | None = Field(default=None, ge=0)
    context_snapshot: dict[str, JsonValue] = Field(default_factory=dict)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    _times = field_validator("started_at", "completed_at")(require_aware)


class AgentRunUpdate(StrictSchema):
    status: AgentRunStatus | None = None
    input_message_id: UUID | None = None
    output_message_id: UUID | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    failure_code: str | None = Field(default=None, max_length=100)
    failure_message: str | None = Field(default=None, max_length=2000)
    model_provider: str | None = Field(default=None, max_length=100)
    model_name: str | None = Field(default=None, max_length=200)
    prompt_tokens: int | None = Field(default=None, ge=0)
    completion_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
    estimated_cost_gbp: Decimal | None = Field(default=None, ge=0)
    latency_ms: int | None = Field(default=None, ge=0)
    context_snapshot: dict[str, JsonValue] | None = None
    metadata: dict[str, JsonValue] | None = None

    _times = field_validator("started_at", "completed_at")(require_aware)


class AgentRunRead(AgentReadSchema):
    id: UUID
    conversation_id: UUID
    agent_id: UUID
    triggered_by_id: UUID
    status: AgentRunStatus
    input_message_id: UUID | None
    output_message_id: UUID | None
    started_at: datetime | None
    completed_at: datetime | None
    failure_code: str | None
    failure_message: str | None
    model_provider: str | None
    model_name: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    estimated_cost_gbp: Decimal | None
    latency_ms: int | None
    context_snapshot: dict[str, JsonValue]
    metadata: dict[str, JsonValue] = Field(
        validation_alias=AliasChoices("metadata", "metadata_")
    )
    created_at: datetime
    updated_at: datetime


class AgentRunFilter(AgentFilter):
    conversation_id: UUID | None = None
    agent_id: UUID | None = None
    triggered_by_id: UUID | None = None
    status: AgentRunStatus | None = None


class AgentRunList(AgentList[AgentRunRead]):
    pass


class AgentToolDefinitionCreate(StrictSchema):
    slug: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=2000)
    category: str = Field(min_length=1, max_length=100)
    risk_level: AgentRiskLevel
    execution_mode: ToolExecutionMode
    requires_approval: bool = False
    is_enabled: bool = True
    input_schema: dict[str, JsonValue] = Field(default_factory=dict)
    output_schema: dict[str, JsonValue] = Field(default_factory=dict)

    _slug = field_validator("slug")(normalize_slug)


class AgentToolDefinitionUpdate(StrictSchema):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, min_length=1, max_length=2000)
    category: str | None = Field(default=None, min_length=1, max_length=100)
    risk_level: AgentRiskLevel | None = None
    execution_mode: ToolExecutionMode | None = None
    requires_approval: bool | None = None
    is_enabled: bool | None = None
    input_schema: dict[str, JsonValue] | None = None
    output_schema: dict[str, JsonValue] | None = None


class AgentToolDefinitionRead(AgentReadSchema):
    id: UUID
    slug: str
    name: str
    description: str
    category: str
    risk_level: AgentRiskLevel
    execution_mode: ToolExecutionMode
    requires_approval: bool
    is_enabled: bool
    input_schema: dict[str, JsonValue]
    output_schema: dict[str, JsonValue]
    created_at: datetime
    updated_at: datetime


class AgentToolDefinitionFilter(AgentFilter):
    slug: str | None = Field(default=None, max_length=120)
    category: str | None = Field(default=None, max_length=100)
    risk_level: AgentRiskLevel | None = None
    execution_mode: ToolExecutionMode | None = None
    is_enabled: bool | None = None

    _slug = field_validator("slug")(normalize_slug)


class AgentToolDefinitionList(AgentList[AgentToolDefinitionRead]):
    pass


class AgentToolExecutionCreate(StrictSchema):
    run_id: UUID
    tool_id: UUID
    approval_id: UUID | None = None
    status: ToolExecutionStatus = ToolExecutionStatus.PENDING
    input_payload: dict[str, JsonValue] = Field(default_factory=dict)
    output_payload: dict[str, JsonValue] | None = None
    error_code: str | None = Field(default=None, max_length=100)
    error_message: str | None = Field(default=None, max_length=2000)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = Field(default=None, ge=0)

    _times = field_validator("started_at", "completed_at")(require_aware)


class AgentToolExecutionUpdate(StrictSchema):
    approval_id: UUID | None = None
    status: ToolExecutionStatus | None = None
    output_payload: dict[str, JsonValue] | None = None
    error_code: str | None = Field(default=None, max_length=100)
    error_message: str | None = Field(default=None, max_length=2000)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_ms: int | None = Field(default=None, ge=0)

    _times = field_validator("started_at", "completed_at")(require_aware)


class AgentToolExecutionRead(AgentReadSchema):
    id: UUID
    run_id: UUID
    tool_id: UUID
    approval_id: UUID | None
    status: ToolExecutionStatus
    input_payload: dict[str, JsonValue]
    output_payload: dict[str, JsonValue] | None
    error_code: str | None
    error_message: str | None
    started_at: datetime | None
    completed_at: datetime | None
    duration_ms: int | None
    created_at: datetime
    updated_at: datetime


class AgentToolExecutionFilter(AgentFilter):
    run_id: UUID | None = None
    tool_id: UUID | None = None
    status: ToolExecutionStatus | None = None


class AgentToolExecutionList(AgentList[AgentToolExecutionRead]):
    pass


class AgentApprovalCreate(StrictSchema):
    run_id: UUID
    tool_execution_id: UUID | None = None
    requested_by_id: UUID
    decided_by_id: UUID | None = None
    action_type: str = Field(min_length=1, max_length=150)
    risk_level: AgentRiskLevel
    status: AgentApprovalStatus = AgentApprovalStatus.PENDING
    reason: str = Field(min_length=1, max_length=2000)
    request_payload: dict[str, JsonValue] = Field(default_factory=dict)
    decision_note: str | None = Field(default=None, max_length=2000)
    requested_at: datetime
    decided_at: datetime | None = None
    expires_at: datetime | None = None

    _times = field_validator("requested_at", "decided_at", "expires_at")(
        require_aware
    )


class AgentApprovalUpdate(StrictSchema):
    decided_by_id: UUID | None = None
    status: AgentApprovalStatus | None = None
    decision_note: str | None = Field(default=None, max_length=2000)
    decided_at: datetime | None = None
    expires_at: datetime | None = None

    _times = field_validator("decided_at", "expires_at")(require_aware)


class AgentApprovalRead(AgentReadSchema):
    id: UUID
    run_id: UUID
    tool_execution_id: UUID | None
    requested_by_id: UUID
    decided_by_id: UUID | None
    action_type: str
    risk_level: AgentRiskLevel
    status: AgentApprovalStatus
    reason: str
    request_payload: dict[str, JsonValue]
    decision_note: str | None
    requested_at: datetime
    decided_at: datetime | None
    expires_at: datetime | None


class AgentApprovalFilter(AgentFilter):
    run_id: UUID | None = None
    status: AgentApprovalStatus | None = None
    requested_by_id: UUID | None = None
    decided_by_id: UUID | None = None
    unexpired_only: bool = False


class AgentApprovalList(AgentList[AgentApprovalRead]):
    pass


class AgentMemoryCreate(StrictSchema):
    owner_id: UUID | None = None
    agent_id: UUID | None = None
    conversation_id: UUID | None = None
    memory_type: AgentMemoryType
    scope: AgentMemoryScope
    key: str = Field(min_length=1, max_length=200)
    value: str = Field(min_length=1, max_length=100000)
    importance: int = Field(default=3, ge=1, le=5)
    is_active: bool = True
    source_message_id: UUID | None = None
    expires_at: datetime | None = None
    created_by_id: UUID | None = None

    _key = field_validator("key")(normalize_memory_key)
    _expires = field_validator("expires_at")(require_aware)


class AgentMemoryUpdate(StrictSchema):
    value: str | None = Field(default=None, min_length=1, max_length=100000)
    importance: int | None = Field(default=None, ge=1, le=5)
    is_active: bool | None = None
    expires_at: datetime | None = None

    _expires = field_validator("expires_at")(require_aware)


class AgentMemoryRead(AgentReadSchema):
    id: UUID
    owner_id: UUID | None
    agent_id: UUID | None
    conversation_id: UUID | None
    memory_type: AgentMemoryType
    scope: AgentMemoryScope
    key: str
    value: str
    importance: int
    is_active: bool
    source_message_id: UUID | None
    expires_at: datetime | None
    created_by_id: UUID | None
    created_at: datetime
    updated_at: datetime


class AgentMemoryFilter(AgentFilter):
    owner_id: UUID | None = None
    agent_id: UUID | None = None
    conversation_id: UUID | None = None
    memory_type: AgentMemoryType | None = None
    scope: AgentMemoryScope | None = None
    key: str | None = Field(default=None, max_length=200)
    active_unexpired_only: bool = True

    _key = field_validator("key")(normalize_memory_key)


class AgentMemoryList(AgentList[AgentMemoryRead]):
    pass


class AgentContextSnapshotCreate(StrictSchema):
    run_id: UUID
    user_context: dict[str, JsonValue] = Field(default_factory=dict)
    permission_context: dict[str, JsonValue] = Field(default_factory=dict)
    project_context: dict[str, JsonValue] = Field(default_factory=dict)
    task_context: dict[str, JsonValue] = Field(default_factory=dict)
    crm_context: dict[str, JsonValue] = Field(default_factory=dict)
    outreach_context: dict[str, JsonValue] = Field(default_factory=dict)
    notification_context: dict[str, JsonValue] = Field(default_factory=dict)
    memory_context: dict[str, JsonValue] = Field(default_factory=dict)


class AgentContextSnapshotUpdate(StrictSchema):
    user_context: dict[str, JsonValue] | None = None
    permission_context: dict[str, JsonValue] | None = None
    project_context: dict[str, JsonValue] | None = None
    task_context: dict[str, JsonValue] | None = None
    crm_context: dict[str, JsonValue] | None = None
    outreach_context: dict[str, JsonValue] | None = None
    notification_context: dict[str, JsonValue] | None = None
    memory_context: dict[str, JsonValue] | None = None


class AgentContextSnapshotRead(AgentReadSchema):
    id: UUID
    run_id: UUID
    user_context: dict[str, JsonValue]
    permission_context: dict[str, JsonValue]
    project_context: dict[str, JsonValue]
    task_context: dict[str, JsonValue]
    crm_context: dict[str, JsonValue]
    outreach_context: dict[str, JsonValue]
    notification_context: dict[str, JsonValue]
    memory_context: dict[str, JsonValue]
    created_at: datetime


class AgentContextSnapshotFilter(AgentFilter):
    run_id: UUID | None = None


class AgentContextSnapshotList(AgentList[AgentContextSnapshotRead]):
    pass


class AgentAttachmentCreate(StrictSchema):
    conversation_id: UUID
    message_id: UUID | None = None
    uploaded_by_id: UUID
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=1, max_length=200)
    size_bytes: int = Field(ge=0)
    storage_key: str = Field(min_length=1, max_length=1000)
    checksum_sha256: str | None = Field(
        default=None, min_length=64, max_length=64
    )
    status: AgentAttachmentStatus = AgentAttachmentStatus.PENDING
    metadata: dict[str, JsonValue] = Field(default_factory=dict)


class AgentAttachmentUpdate(StrictSchema):
    message_id: UUID | None = None
    filename: str | None = Field(default=None, min_length=1, max_length=255)
    content_type: str | None = Field(default=None, min_length=1, max_length=200)
    status: AgentAttachmentStatus | None = None
    checksum_sha256: str | None = Field(
        default=None, min_length=64, max_length=64
    )
    metadata: dict[str, JsonValue] | None = None


class AgentAttachmentRead(AgentReadSchema):
    id: UUID
    conversation_id: UUID
    message_id: UUID | None
    uploaded_by_id: UUID
    filename: str
    content_type: str
    size_bytes: int
    storage_key: str
    checksum_sha256: str | None
    status: AgentAttachmentStatus
    metadata: dict[str, JsonValue] = Field(
        validation_alias=AliasChoices("metadata", "metadata_")
    )
    created_at: datetime
    updated_at: datetime


class AgentAttachmentFilter(AgentFilter):
    conversation_id: UUID | None = None
    message_id: UUID | None = None
    uploaded_by_id: UUID | None = None
    status: AgentAttachmentStatus | None = None


class AgentAttachmentList(AgentList[AgentAttachmentRead]):
    pass
