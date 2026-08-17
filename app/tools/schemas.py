from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ToolCategory(str, Enum):
    PROJECTS = "projects"
    TASKS = "tasks"
    CRM = "crm"
    ACTIVITIES = "activities"
    NOTIFICATIONS = "notifications"
    MEMORY = "memory"
    DASHBOARD = "dashboard"
    PORTFOLIO = "portfolio"
    HEALTH = "health"
    MARKET_DATA = "market_data"
    WEATHER = "weather"
    RUNTIME = "runtime"


class ToolCapability(str, Enum):
    SEARCH = "search"
    SUMMARY = "summary"
    ANALYTICS = "analytics"
    READ = "read"
    WRITE = "write"
    HEALTH = "health"


class ToolPermission(str, Enum):
    READ = "read"
    WRITE = "write"
    ADMIN = "admin"
    AUDIT = "audit"


class ToolHealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class PermissionOutcome(str, Enum):
    ALLOWED = "allowed"
    DENIED = "denied"


class ToolHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: ToolHealthStatus
    available: bool
    checked_at: datetime
    detail: str | None = None


class ToolMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    category: ToolCategory
    version: str
    permissions: frozenset[ToolPermission]
    capabilities: frozenset[ToolCapability]
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]


class ToolResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool
    data: Any = None
    failure: str | None = None
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    execution_time_ms: float = Field(ge=0)
    tool_version: str
    correlation_id: UUID


class ToolExecutionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool: str
    agent_id: UUID
    user_id: UUID
    run_id: UUID
    duration_ms: float = Field(ge=0)
    result: str
    permission_outcome: PermissionOutcome
    timestamp: datetime
    correlation_id: UUID


class ToolExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ToolBatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    results: list[ToolResult]
    execution_mode: str
    correlation_id: UUID
