from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.database.models import (
    AgentConversation,
    AgentDefinition,
    AgentRun,
    User,
)
from app.tools.exceptions import ToolValidationError
from app.tools.schemas import ToolPermission


@dataclass(frozen=True, slots=True)
class ToolExecutionContext:
    current_user: User
    current_agent: AgentDefinition
    conversation: AgentConversation
    run: AgentRun
    permissions: frozenset[ToolPermission]
    correlation_id: UUID = field(default_factory=uuid4)
    timezone: str = "UTC"
    locale: str = "en-GB"
    current_timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def __post_init__(self) -> None:
        if self.current_timestamp.tzinfo is None:
            raise ToolValidationError(
                "current_timestamp must include a timezone"
            )
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as error:
            raise ToolValidationError("Invalid execution timezone") from error
        if self.run.conversation_id != self.conversation.id:
            raise ToolValidationError("Run does not belong to conversation")
        if self.run.agent_id != self.current_agent.id:
            raise ToolValidationError("Run does not belong to current agent")
        if self.run.triggered_by_id != self.current_user.id:
            raise ToolValidationError("Run does not belong to current user")
        if self.conversation.agent_id != self.current_agent.id:
            raise ToolValidationError(
                "Conversation does not belong to current agent"
            )
