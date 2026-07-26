from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.database.models import (
    AgentConversation,
    AgentDefinition,
    AgentRun,
    User,
)
from app.integrations.exceptions import IntegrationValidationError
from app.integrations.schemas import (
    IntegrationEnvironment,
    IntegrationPermission,
)


@dataclass(frozen=True, slots=True)
class IntegrationExecutionContext:
    user: User
    agent: AgentDefinition
    conversation: AgentConversation
    run: AgentRun
    user_permissions: frozenset[IntegrationPermission]
    agent_permissions: frozenset[IntegrationPermission]
    integration_permissions: frozenset[IntegrationPermission]
    correlation_id: UUID = field(default_factory=uuid4)
    timezone: str = "UTC"
    locale: str = "en-GB"
    environment: IntegrationEnvironment = IntegrationEnvironment.TEST

    def __post_init__(self) -> None:
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as error:
            raise IntegrationValidationError(
                "Invalid integration timezone"
            ) from error
        if self.run.conversation_id != self.conversation.id:
            raise IntegrationValidationError(
                "Run does not belong to conversation"
            )
        if self.run.agent_id != self.agent.id:
            raise IntegrationValidationError(
                "Run does not belong to agent"
            )
        if self.run.triggered_by_id != self.user.id:
            raise IntegrationValidationError(
                "Run does not belong to user"
            )
        if self.conversation.agent_id != self.agent.id:
            raise IntegrationValidationError(
                "Conversation does not belong to agent"
            )

    @property
    def permissions(self) -> frozenset[IntegrationPermission]:
        return (
            self.user_permissions
            & self.agent_permissions
            & self.integration_permissions
        )
