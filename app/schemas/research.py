from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from app.schemas.auth import StrictSchema


class WorkspaceSelector(StrictSchema):
    workspace_id: UUID | None = None


class ExperimentCreate(WorkspaceSelector):
    name: str = Field(min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=4_000)


class DatasetCreate(StrictSchema):
    name: str = Field(min_length=1, max_length=160)
    source: str = Field(min_length=1, max_length=240)
    observed_at: datetime
    provenance: dict[str, Any]
    metadata: dict[str, Any] = Field(default_factory=dict)


class ModelCreate(StrictSchema):
    name: str = Field(min_length=1, max_length=160)
    version: str = Field(min_length=1, max_length=80)
    provenance: dict[str, Any]


class RunCreate(StrictSchema):
    dataset_id: UUID | None = None
    model_id: UUID | None = None


class ResearchCreate(WorkspaceSelector):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(min_length=1, max_length=100_000)
    source: str = Field(min_length=1, max_length=240)
    observed_at: datetime
    provenance: dict[str, Any]
    tags: list[str] = Field(default_factory=list, max_length=30)


class ExperimentRead(StrictSchema):
    id: UUID
    tenant_id: UUID
    workspace_id: UUID
    account_id: UUID
    created_by_id: UUID
    name: str
    description: str | None
    status: str
    provenance: dict[str, Any]
    created_at: datetime
    updated_at: datetime


class DatasetRead(StrictSchema):
    id: UUID
    experiment_id: UUID
    workspace_id: UUID
    account_id: UUID
    name: str
    source: str
    observed_at: datetime
    status: str
    provenance: dict[str, Any]
    metadata: dict[str, Any]
    created_at: datetime


class ModelRead(StrictSchema):
    id: UUID
    experiment_id: UUID
    workspace_id: UUID
    account_id: UUID
    name: str
    version: str
    status: str
    provenance: dict[str, Any]
    created_at: datetime


class RunRead(StrictSchema):
    id: UUID
    experiment_id: UUID
    workspace_id: UUID
    account_id: UUID
    dataset_id: UUID | None
    model_id: UUID | None
    status: str
    result: dict[str, Any]
    provenance: dict[str, Any]
    failure_reason: str | None
    created_at: datetime
    completed_at: datetime | None


class ResearchRead(StrictSchema):
    id: UUID
    tenant_id: UUID
    workspace_id: UUID
    account_id: UUID
    created_by_id: UUID
    title: str
    content: str
    source: str
    observed_at: datetime
    status: str
    provenance: dict[str, Any]
    tags: list[str]
    created_at: datetime
    updated_at: datetime
