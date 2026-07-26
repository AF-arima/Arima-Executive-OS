from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.background.exceptions import BackgroundValidationError
from app.background.schemas import (
    BackgroundPermission,
    BackgroundTriggerSource,
    Environment,
    ScheduleDefinition,
)
from app.database.models import (
    AgentConversation,
    AgentDefinition,
    AgentRun,
    User,
)

if TYPE_CHECKING:
    from app.background.base import BackgroundJob


@dataclass(frozen=True, slots=True)
class BackgroundExecutionContext:
    user: User
    agent: AgentDefinition
    conversation: AgentConversation
    run: AgentRun
    job: BackgroundJob
    schedule: ScheduleDefinition | None
    user_permissions: frozenset[BackgroundPermission]
    agent_permissions: frozenset[BackgroundPermission]
    job_permissions: frozenset[BackgroundPermission]
    tool_permissions: frozenset[BackgroundPermission]
    integration_permissions: frozenset[BackgroundPermission]
    current_timestamp: datetime
    trigger_source: BackgroundTriggerSource
    correlation_id: UUID = field(default_factory=uuid4)
    timezone: str = "UTC"
    locale: str = "en-GB"
    environment: Environment = Environment.TEST

    def __post_init__(self) -> None:
        if self.current_timestamp.tzinfo is None:
            raise BackgroundValidationError(
                "Background timestamp must be timezone-aware"
            )
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as error:
            raise BackgroundValidationError(
                "Invalid background timezone"
            ) from error
        if self.run.conversation_id != self.conversation.id:
            raise BackgroundValidationError(
                "Run does not belong to conversation"
            )
        if self.run.agent_id != self.agent.id:
            raise BackgroundValidationError("Run does not belong to agent")
        if self.run.triggered_by_id != self.user.id:
            raise BackgroundValidationError("Run does not belong to user")
        if self.conversation.agent_id != self.agent.id:
            raise BackgroundValidationError(
                "Conversation does not belong to agent"
            )

    @property
    def permissions(self) -> frozenset[BackgroundPermission]:
        return (
            self.user_permissions
            & self.agent_permissions
            & self.job_permissions
            & self.tool_permissions
            & self.integration_permissions
        )
