from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class ExperienceChamber(str, Enum):
    EXECUTIVE = "executive"
    PORTFOLIO = "portfolio"
    QUANT = "quant"
    GROWTH = "growth"
    PROJECTS = "projects"
    PUBLICATIONS = "publications"
    APPROVALS = "approvals"
    HEALTH = "health"


class ExperienceEventPriority(str, Enum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    CRITICAL = "critical"


class ExperienceEventType(str, Enum):
    AVATAR_STATE_CHANGED = "avatar_state_changed"
    NEURAL_ACTIVITY_STARTED = "neural_activity_started"
    NEURAL_ACTIVITY_COMPLETED = "neural_activity_completed"
    CHAMBER_TRANSITION_REQUESTED = "chamber_transition_requested"
    DATA_OBJECT_CREATED = "data_object_created"
    DATA_OBJECT_UPDATED = "data_object_updated"
    DATA_OBJECT_DISMISSED = "data_object_dismissed"
    TASK_VISUALISATION_REQUESTED = "task_visualisation_requested"
    WATCHLIST_VISUALISATION_REQUESTED = "watchlist_visualisation_requested"
    PERFORMANCE_VISUALISATION_REQUESTED = (
        "performance_visualisation_requested"
    )
    APPROVAL_VISUALISATION_REQUESTED = "approval_visualisation_requested"
    WARNING_VISUALISATION_REQUESTED = "warning_visualisation_requested"
    SYSTEM_PULSE = "system_pulse"
    BACKGROUND_JOB_COMPLETED = "background_job_completed"


class ExperienceEvent(BaseModel):
    """A visual instruction derived from an already-authorized outcome."""

    model_config = ConfigDict(extra="forbid")

    event_id: UUID = Field(default_factory=uuid4)
    session_id: UUID
    correlation_id: UUID
    timestamp: datetime
    type: ExperienceEventType
    priority: ExperienceEventPriority = ExperienceEventPriority.NORMAL
    source: str = Field(min_length=1, max_length=120)
    target_chamber: ExperienceChamber | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    duration_hint: int | None = Field(default=None, ge=0, le=120_000)
    dismissible: bool = True
    requires_attention: bool = False
