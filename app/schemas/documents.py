from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class CustomerDocumentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    target_user_id: UUID
    uploaded_by_id: UUID
    filename: str
    content_type: str
    size_bytes: int
    title: str
    description: str | None
    provenance: dict[str, object]
    status: str
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None


class DocumentStatusChange(BaseModel):
    status: str = Field(pattern="^(archived|revoked)$")
    reason: str = Field(min_length=1, max_length=500)
