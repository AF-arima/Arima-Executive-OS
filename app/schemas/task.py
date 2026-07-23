from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.database.models import TaskPriority, TaskStatus
from app.schemas.auth import StrictSchema


class TaskSortField(str, Enum):
    CREATED_AT = "created_at"
    UPDATED_AT = "updated_at"
    DUE_DATE = "due_date"
    PRIORITY = "priority"


class TaskCreate(StrictSchema):
    title: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=5000)
    status: TaskStatus = TaskStatus.TODO
    priority: TaskPriority = TaskPriority.MEDIUM
    due_date: datetime | None = None
    project_id: UUID
    assigned_to: UUID | None = None

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Task title cannot be blank")
        return normalized

    @field_validator("due_date")
    @classmethod
    def validate_due_date(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        return require_timezone(value)


class TaskUpdate(StrictSchema):
    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=5000)
    status: TaskStatus | None = None
    priority: TaskPriority | None = None
    due_date: datetime | None = None
    project_id: UUID | None = None
    assigned_to: UUID | None = None

    @field_validator("title")
    @classmethod
    def normalize_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("Task title cannot be blank")
        return normalized

    @field_validator("due_date")
    @classmethod
    def validate_due_date(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        return require_timezone(value)

    @model_validator(mode="after")
    def reject_null_required_fields(self) -> "TaskUpdate":
        for field_name in ("title", "status", "priority", "project_id"):
            if (
                field_name in self.model_fields_set
                and getattr(self, field_name) is None
            ):
                raise ValueError(f"{field_name} cannot be null")
        return self


class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")

    id: UUID
    title: str
    description: str | None
    status: TaskStatus
    priority: TaskPriority
    due_date: datetime | None
    completed_at: datetime | None
    project_id: UUID
    assigned_to: UUID | None = Field(
        validation_alias=AliasChoices("assigned_to", "assignee_id")
    )
    created_by: UUID
    created_at: datetime
    updated_at: datetime


class TaskListResponse(BaseModel):
    items: list[TaskResponse]
    total: int
    limit: int
    offset: int


def require_timezone(value: datetime | None) -> datetime | None:
    if value is not None and (
        value.tzinfo is None or value.utcoffset() is None
    ):
        raise ValueError("due_date must include a timezone")
    return value
