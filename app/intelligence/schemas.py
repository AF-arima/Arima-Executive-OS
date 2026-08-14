from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class IntelligenceSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def require_aware(value: datetime | None) -> datetime | None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError("Timestamp must be timezone-aware")
    return value


class KnowledgeSourceInput(IntelligenceSchema):
    source_type: str = Field(min_length=1, max_length=50)
    external_id: str = Field(min_length=1, max_length=300)
    name: str = Field(min_length=1, max_length=300)
    source_uri: str | None = Field(default=None, max_length=2000)
    freshness_required: bool = False
    max_age_seconds: int | None = Field(default=None, ge=1, le=2_592_000)


class KnowledgeDocumentInput(IntelligenceSchema):
    external_id: str = Field(min_length=1, max_length=500)
    title: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1, max_length=2_000_000)
    source_observed_at: datetime
    expires_at: datetime | None = None
    provenance: dict[str, object]

    _observed = field_validator("source_observed_at")(require_aware)
    _expires = field_validator("expires_at")(require_aware)


class IngestedKnowledge(IntelligenceSchema):
    source_id: UUID
    document_id: UUID
    chunk_ids: tuple[UUID, ...]
    content_hash: str


class RetrievalQuery(IntelligenceSchema):
    text: str = Field(min_length=1, max_length=20_000)
    limit: int = Field(default=10, ge=1, le=50)
    require_fresh: bool = True


class RetrievedKnowledge(IntelligenceSchema):
    evidence_id: UUID
    chunk_id: UUID
    document_id: UUID
    source_id: UUID
    content: str
    rank: int = Field(ge=1)
    score: float = Field(ge=0)
    source_observed_at: datetime
    provenance: dict[str, object]

    _observed = field_validator("source_observed_at")(require_aware)


class WorkflowResult(IntelligenceSchema):
    conversation_id: UUID
    run_id: UUID
    output_message_id: UUID
    response: str
    evidence_ids: tuple[UUID, ...]


class AuditChain(IntelligenceSchema):
    user_id: UUID
    workspace_id: UUID
    conversation_id: UUID
    agent_id: UUID
    run_id: UUID
    run_status: str
    retrieved_context_ids: tuple[UUID, ...]
    tool_execution_ids: tuple[UUID, ...]
    output_message_id: UUID | None
    resulting_action_ids: tuple[UUID, ...]
