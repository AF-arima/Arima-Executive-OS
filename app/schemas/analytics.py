from datetime import datetime, timezone
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.database.models import ProjectStatus, TaskPriority, TaskStatus


class AnalyticsInterval(str, Enum):
    DAY = "day"
    WEEK = "week"
    MONTH = "month"


class ProjectAnalyticsSortField(str, Enum):
    NAME = "name"
    CREATED_AT = "created_at"
    UPDATED_AT = "updated_at"
    TOTAL_TASKS = "total_tasks"
    COMPLETED_TASKS = "completed_tasks"
    OVERDUE_TASKS = "overdue_tasks"
    COMPLETION_RATE = "completion_rate"
    LAST_ACTIVITY_AT = "last_activity_at"


class WorkloadSortField(str, Enum):
    EMAIL = "email"
    ACTIVE_TASK_COUNT = "active_task_count"
    OVERDUE_TASK_COUNT = "overdue_task_count"
    COMPLETED_TASK_COUNT = "completed_task_count"
    WORKLOAD_SCORE = "workload_score"


class DashboardProvenance(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["server_persisted"]
    record_type: Literal["workspace"]
    workspace_id: UUID


class DashboardSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provenance: DashboardProvenance
    total_projects: int = Field(ge=0)
    active_projects: int = Field(ge=0)
    archived_projects: int = Field(ge=0)
    projects_by_status: dict[ProjectStatus, int]
    total_tasks: int = Field(ge=0)
    tasks_by_status: dict[TaskStatus, int]
    tasks_by_priority: dict[TaskPriority, int]
    completed_tasks: int = Field(ge=0)
    overdue_tasks: int = Field(ge=0)
    unassigned_tasks: int = Field(ge=0)
    completion_rate: float = Field(ge=0, le=1)
    overdue_rate: float = Field(ge=0, le=1)
    average_completion_time_hours: float = Field(ge=0)
    tasks_due_next_7_days: int = Field(ge=0)
    tasks_due_next_30_days: int = Field(ge=0)
    active_users: int = Field(ge=0)
    recent_activity_count: int = Field(ge=0)
    generated_at: datetime
    range_start: datetime
    range_end: datetime


class ProjectAnalyticsItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: UUID
    name: str
    status: ProjectStatus
    owner_id: UUID
    archived: bool
    total_tasks: int = Field(ge=0)
    completed_tasks: int = Field(ge=0)
    overdue_tasks: int = Field(ge=0)
    unassigned_tasks: int = Field(ge=0)
    completion_rate: float = Field(ge=0, le=1)
    overdue_rate: float = Field(ge=0, le=1)
    average_completion_time_hours: float = Field(ge=0)
    last_activity_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @field_validator(
        "last_activity_at",
        "created_at",
        "updated_at",
        mode="after",
    )
    @classmethod
    def ensure_timezone(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value


class ProjectAnalyticsList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[ProjectAnalyticsItem]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)


class TimeSeriesPoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period_start: datetime
    value: int = Field(ge=0)


class TaskAnalyticsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    totals: int = Field(ge=0)
    status_breakdown: dict[TaskStatus, int]
    priority_breakdown: dict[TaskPriority, int]
    overdue_count: int = Field(ge=0)
    completed_count: int = Field(ge=0)
    created_count: int = Field(ge=0)
    completion_rate: float = Field(ge=0, le=1)
    average_completion_time_hours: float = Field(ge=0)
    throughput_series: list[TimeSeriesPoint]
    created_series: list[TimeSeriesPoint]
    overdue_series: list[TimeSeriesPoint]
    generated_at: datetime
    range_start: datetime
    range_end: datetime
    interval: AnalyticsInterval


class WorkloadAnalyticsItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: UUID
    email: str
    active_task_count: int = Field(ge=0)
    overdue_task_count: int = Field(ge=0)
    completed_task_count: int = Field(ge=0)
    urgent_task_count: int = Field(ge=0)
    high_priority_task_count: int = Field(ge=0)
    due_next_7_days: int = Field(ge=0)
    average_completion_time_hours: float = Field(ge=0)
    workload_score: int = Field(
        ge=0,
        description=(
            "active tasks + 3×overdue + 2×urgent + high priority "
            "+ tasks due within seven days"
        ),
    )


class WorkloadAnalyticsList(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[WorkloadAnalyticsItem]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
