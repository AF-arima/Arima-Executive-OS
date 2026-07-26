from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any
from uuid import UUID


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    run_id: UUID
    snapshot_id: UUID
    user: dict[str, Any]
    conversation: dict[str, Any]
    messages: tuple[dict[str, Any], ...]
    memory: tuple[dict[str, Any], ...]
    permissions: dict[str, Any]
    projects: tuple[dict[str, Any], ...]
    tasks: tuple[dict[str, Any], ...]
    crm: dict[str, Any]
    outreach: dict[str, Any]
    notifications: tuple[dict[str, Any], ...]
    previous_runs: tuple[dict[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": str(self.run_id),
            "snapshot_id": str(self.snapshot_id),
            "user": self.user,
            "conversation": self.conversation,
            "messages": list(self.messages),
            "memory": list(self.memory),
            "permissions": self.permissions,
            "projects": list(self.projects),
            "tasks": list(self.tasks),
            "crm": self.crm,
            "outreach": self.outreach,
            "notifications": list(self.notifications),
            "previous_runs": list(self.previous_runs),
        }


@dataclass(frozen=True, slots=True)
class PromptMessage:
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class StructuredPrompt:
    system_instructions: str
    conversation: tuple[PromptMessage, ...]
    memory: tuple[str, ...]
    tool_outputs: tuple[dict[str, Any], ...]
    context: dict[str, Any]

    def text(self) -> str:
        sections = [self.system_instructions]
        sections.extend(
            f"{message.role}: {message.content}"
            for message in self.conversation
        )
        sections.extend(f"memory: {item}" for item in self.memory)
        return "\n".join(sections)


@dataclass(frozen=True, slots=True)
class ToolInvocation:
    slug: str
    payload: dict[str, Any] = field(default_factory=dict)
    execution_id: UUID | None = None
    approval_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class ToolResult:
    execution_id: UUID
    slug: str
    output: dict[str, Any]
    duration_ms: int


@dataclass(frozen=True, slots=True)
class ProviderRequest:
    run_id: UUID
    prompt: StructuredPrompt
    tool_results: tuple[ToolResult, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    content: str
    prompt_tokens: int
    completion_tokens: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    available: bool
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CostBreakdown:
    prompt_cost_gbp: Decimal
    completion_cost_gbp: Decimal
    total_cost_gbp: Decimal


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    run_id: UUID
    output_message_id: UUID
    provider_name: str
    tool_results: tuple[ToolResult, ...]
    prompt_tokens: int
    completion_tokens: int
    estimated_cost_gbp: Decimal


@dataclass(frozen=True, slots=True)
class RetryPreparation:
    previous_run_id: UUID
    next_attempt: int
    retryable: bool
    backoff_metadata: dict[str, Any]
