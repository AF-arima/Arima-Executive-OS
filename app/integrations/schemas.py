from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class IntegrationProvider(str, Enum):
    GOOGLE = "google"
    MICROSOFT = "microsoft"
    SLACK = "slack"
    DISCORD = "discord"
    NOTION = "notion"
    AIRTABLE = "airtable"
    GITHUB = "github"
    GITLAB = "gitlab"
    WEB = "web"
    FINANCE = "finance"


class IntegrationCapability(str, Enum):
    READ = "read"
    WRITE = "write"
    SEARCH = "search"
    MESSAGING = "messaging"
    CALENDAR = "calendar"
    CONTACTS = "contacts"
    FILES = "files"
    COLLABORATION = "collaboration"
    DATA = "data"
    MARKET_DATA = "market_data"


class IntegrationPermission(str, Enum):
    READ = "read"
    WRITE = "write"
    ADMIN = "admin"
    APPROVAL_REQUIRED = "approval_required"
    SENSITIVE_DATA = "sensitive_data"


class ApprovalPolicy(str, Enum):
    NONE = "none"
    USER = "user"
    ADMIN = "admin"
    MULTI_STAGE = "multi_stage"


class ApprovalOutcome(str, Enum):
    NOT_REQUIRED = "not_required"
    APPROVED = "approved"
    DENIED = "denied"
    PENDING = "pending"


class IntegrationEnvironment(str, Enum):
    DEVELOPMENT = "development"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class ConnectorHealthState(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class ConnectorOperation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=500)
    permissions: frozenset[IntegrationPermission]
    approval_policy: ApprovalPolicy = ApprovalPolicy.NONE
    sensitive_data: bool = False


class ConnectorHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: bool
    latency_ms: float | None = Field(default=None, ge=0)
    last_successful_execution: datetime | None = None
    last_failed_execution: datetime | None = None
    state: ConnectorHealthState
    checked_at: datetime
    detail: str | None = None


class ConnectorMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    version: str
    description: str
    provider: IntegrationProvider
    operations: tuple[ConnectorOperation, ...]
    capabilities: frozenset[IntegrationCapability]
    mock: bool = True


class ApprovalGrant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy: ApprovalPolicy
    outcome: ApprovalOutcome
    approved_by: UUID | None = None
    approved_at: datetime | None = None
    completed_stages: int = Field(default=0, ge=0)


class IntegrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connector: str
    operation: str
    payload: dict[str, Any] = Field(default_factory=dict)
    version: str | None = None
    approval: ApprovalGrant | None = None
    dry_run: bool = False


class ValidatedIntegrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: ConnectorOperation
    payload: dict[str, Any]


class ConnectorResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool
    data: Any = None
    failure: str | None = None
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    execution_duration_ms: float = Field(ge=0)
    provider: IntegrationProvider
    connector_version: str
    correlation_id: UUID


class IntegrationBatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    results: list[ConnectorResult]
    execution_mode: str
    correlation_id: UUID


class IntegrationExecutionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    connector: str
    provider: IntegrationProvider
    user_id: UUID
    agent_id: UUID
    run_id: UUID
    operation: str
    duration_ms: float = Field(ge=0)
    result: str
    approval_outcome: ApprovalOutcome
    timestamp: datetime
    correlation_id: UUID
