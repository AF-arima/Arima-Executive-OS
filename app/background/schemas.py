from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator

class BackgroundJobType(str, Enum):
    ONE_TIME = "one_time"
    SCHEDULED = "scheduled"
    RECURRING = "recurring"
    CONDITIONAL = "conditional"
    EVENT_TRIGGERED = "event_triggered"
    MANUAL = "manual"
    SYSTEM = "system"


class BackgroundTriggerSource(str, Enum):
    SCHEDULE = "schedule"
    MANUAL = "manual"
    CONDITION = "condition"
    EVENT = "event"
    SYSTEM = "system"
    RETRY = "retry"


class BackgroundJobState(str, Enum):
    PENDING = "pending"
    SCHEDULED = "scheduled"
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PAUSED = "paused"
    BLOCKED = "blocked"
    WAITING_FOR_APPROVAL = "waiting_for_approval"
    EXPIRED = "expired"


class BackgroundJobCategory(str, Enum):
    EXECUTIVE = "executive"
    PROJECTS = "projects"
    TASKS = "tasks"
    CRM = "crm"
    NOTIFICATIONS = "notifications"
    HEALTH = "health"
    PORTFOLIO = "portfolio"
    RESEARCH = "research"
    GROWTH = "growth"
    INTEGRATIONS = "integrations"
    MEMORY = "memory"
    AUDIT = "audit"


class BackgroundCapability(str, Enum):
    INTERNAL_TOOL = "internal_tool"
    INTEGRATION = "integration"
    AGENT_EXECUTION = "agent_execution"
    REVIEW = "review"
    MAINTENANCE = "maintenance"
    HEALTH = "health"


class BackgroundPermission(str, Enum):
    READ = "read"
    WRITE = "write"
    ADMIN = "admin"
    APPROVAL_REQUIRED = "approval_required"
    SENSITIVE_DATA = "sensitive_data"
    EXECUTE_TOOL = "execute_tool"
    EXECUTE_INTEGRATION = "execute_integration"
    EXECUTE_AGENT = "execute_agent"


class ScheduleType(str, Enum):
    ONE_TIME = "one_time"
    RECURRING = "recurring"
    INTERVAL = "interval"
    CALENDAR = "calendar"
    CONDITIONAL = "conditional"


class RecurrenceFrequency(str, Enum):
    MINUTE = "minute"
    HOURLY = "hourly"
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    CUSTOM_INTERVAL = "custom_interval"


class BackgroundHealthState(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


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


class Environment(str, Enum):
    DEVELOPMENT = "development"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class ApprovalGrant(BaseModel):
    model_config = ConfigDict(extra="forbid")

    policy: ApprovalPolicy
    outcome: ApprovalOutcome
    approved_by: UUID | None = None
    approved_at: datetime | None = None
    completed_stages: int = Field(default=0, ge=0)


class ScheduleDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    job_name: str = Field(min_length=1, max_length=150)
    schedule_type: ScheduleType
    timezone: str = "UTC"
    start_at: datetime | None = None
    end_at: datetime | None = None
    next_run_at: datetime | None = None
    last_run_at: datetime | None = None
    maximum_runs: int | None = Field(default=None, ge=1)
    run_count: int = Field(default=0, ge=0)
    enabled: bool = True
    paused: bool = False
    input_payload: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_schedule(self) -> ScheduleDefinition:
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as error:
            raise ValueError("Invalid schedule timezone") from error
        for value in (
            self.start_at,
            self.end_at,
            self.next_run_at,
            self.last_run_at,
        ):
            if value is not None and value.tzinfo is None:
                raise ValueError("Schedule datetimes must be timezone-aware")
        if (
            self.start_at is not None
            and self.end_at is not None
            and self.end_at < self.start_at
        ):
            raise ValueError("Schedule end must not precede start")
        if self.maximum_runs is not None and self.run_count > self.maximum_runs:
            raise ValueError("Schedule run count exceeds maximum")
        return self


class OneTimeSchedule(ScheduleDefinition):
    schedule_type: Literal[ScheduleType.ONE_TIME] = ScheduleType.ONE_TIME
    run_at: datetime


class RecurringSchedule(ScheduleDefinition):
    schedule_type: Literal[ScheduleType.RECURRING] = ScheduleType.RECURRING
    frequency: RecurrenceFrequency
    interval_count: int = Field(default=1, ge=1)


class IntervalSchedule(ScheduleDefinition):
    schedule_type: Literal[ScheduleType.INTERVAL] = ScheduleType.INTERVAL
    interval_seconds: int = Field(ge=1)


class CalendarSchedule(ScheduleDefinition):
    schedule_type: Literal[ScheduleType.CALENDAR] = ScheduleType.CALENDAR
    frequency: RecurrenceFrequency
    hour: int = Field(default=0, ge=0, le=23)
    minute: int = Field(default=0, ge=0, le=59)


class ConditionalSchedule(ScheduleDefinition):
    schedule_type: Literal[ScheduleType.CONDITIONAL] = ScheduleType.CONDITIONAL
    condition: dict[str, Any]
    evaluation_interval_seconds: int = Field(default=300, ge=1)


class BackgroundJobMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    version: str
    description: str
    category: BackgroundJobCategory
    job_type: BackgroundJobType
    permissions: frozenset[BackgroundPermission]
    approval_policy: ApprovalPolicy
    capabilities: frozenset[BackgroundCapability]
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]


class BackgroundHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: bool
    state: BackgroundHealthState
    checked_at: datetime
    last_tick: datetime | None = None
    last_dispatch: datetime | None = None
    last_success: datetime | None = None
    last_failure: datetime | None = None
    active_jobs: int = Field(default=0, ge=0)
    failed_jobs: int = Field(default=0, ge=0)
    blocked_jobs: int = Field(default=0, ge=0)


class BackgroundExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_name: str
    payload: dict[str, Any] = Field(default_factory=dict)
    schedule_id: UUID | None = None
    approval: ApprovalGrant | None = None
    dry_run: bool = False


class BackgroundExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool
    status: BackgroundJobState
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    duration_ms: float = Field(ge=0)
    attempt_number: int = Field(ge=1)
    job_version: str
    correlation_id: UUID
    started_at: datetime
    completed_at: datetime
    next_run_at: datetime | None = None


class BackgroundBatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    results: list[BackgroundExecutionResult]
    execution_mode: str
    correlation_id: UUID


class BackgroundLifecycleRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job: str
    schedule_id: UUID | None
    execution_id: UUID | None
    user_id: UUID
    agent_id: UUID
    trigger: BackgroundTriggerSource
    from_state: BackgroundJobState | None
    to_state: BackgroundJobState
    attempt: int
    duration_ms: float = Field(ge=0)
    result: str
    approval_outcome: ApprovalOutcome
    permission_outcome: str
    timestamp: datetime
    correlation_id: UUID


class JobExecutionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: Literal["mock", "internal_tool", "integration", "agent"]
    target_name: str | None = None
    operation: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    mock_result: dict[str, Any] = Field(default_factory=dict)
