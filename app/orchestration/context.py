from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field

from app.database.models import (
    AgentConversation,
    AgentDefinition,
    AgentRun,
    User,
)
from app.orchestration.exceptions import OrchestrationConfigurationError
from app.orchestration.health import HealthContract
from app.orchestration.schemas import (
    AgentCandidate,
    ExecutedAction,
    OrchestrationRequest,
)


@dataclass(frozen=True, slots=True)
class OrchestrationExecutionContext:
    user: User
    agent: AgentDefinition
    conversation: AgentConversation
    run: AgentRun
    request: OrchestrationRequest
    permissions: frozenset[str]
    agent_candidates: tuple[AgentCandidate, ...] = ()
    correlation_id: UUID = field(default_factory=uuid4)
    timezone: str = "UTC"
    locale: str = "en-GB"
    current_timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def __post_init__(self) -> None:
        if self.current_timestamp.tzinfo is None:
            raise OrchestrationConfigurationError(
                "Orchestration timestamp must be timezone-aware"
            )
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as error:
            raise OrchestrationConfigurationError(
                "Invalid orchestration timezone"
            ) from error
        if self.conversation.owner_id != self.user.id:
            raise OrchestrationConfigurationError(
                "Conversation does not belong to user"
            )
        if self.run.triggered_by_id != self.user.id:
            raise OrchestrationConfigurationError(
                "Run was not triggered by user"
            )
        if self.run.conversation_id != self.conversation.id:
            raise OrchestrationConfigurationError(
                "Run does not belong to conversation"
            )
        if self.run.agent_id != self.agent.id:
            raise OrchestrationConfigurationError(
                "Run does not belong to agent"
            )
        if self.conversation.agent_id != self.agent.id:
            raise OrchestrationConfigurationError(
                "Conversation does not belong to agent"
            )


class BuiltOrchestrationContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    system_prompt: str
    user_profile: dict[str, str]
    agent_instructions: str
    conversation: list[dict[str, str]]
    memories: list[str]
    tool_results: list[dict[str, object]]
    integration_results: list[dict[str, object]]
    background_results: list[dict[str, object]]
    token_count: int = Field(ge=0)
    token_limit: int = Field(ge=1)


class OrchestrationContextBuilder(HealthContract):
    component_name = "context"

    def build(
        self,
        context: OrchestrationExecutionContext,
        *,
        memories: list[str],
        actions: list[ExecutedAction] | None = None,
    ) -> BuiltOrchestrationContext:
        actions = actions or []
        tools = [
            action.output
            for action in actions
            if action.target.value == "tool"
        ]
        integrations = [
            action.output
            for action in actions
            if action.target.value == "integration"
        ]
        background = [
            action.output
            for action in actions
            if action.target.value == "background"
        ]
        conversation = [
            {"role": "user", "content": context.request.content}
        ]
        system_prompt = (
            "Arima Executive OS orchestration. "
            f"Locale={context.locale}; timezone={context.timezone}."
        )
        words = (
            system_prompt
            + " "
            + context.agent.system_instructions
            + " "
            + context.request.content
            + " "
            + " ".join(memories)
        ).split()
        limit = context.request.max_context_tokens
        if len(words) > limit:
            memories = memories[: max(0, limit // 20)]
            words = words[:limit]
        return BuiltOrchestrationContext(
            system_prompt=system_prompt,
            user_profile={
                "id": str(context.user.id),
                "name": (
                    f"{context.user.first_name} "
                    f"{context.user.last_name}"
                ),
                "locale": context.locale,
            },
            agent_instructions=context.agent.system_instructions,
            conversation=conversation,
            memories=memories,
            tool_results=tools,
            integration_results=integrations,
            background_results=background,
            token_count=len(words),
            token_limit=limit,
        )
