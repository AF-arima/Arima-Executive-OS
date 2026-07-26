from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field


class OrchestrationIntent(str, Enum):
    CONVERSATION = "conversation"
    TASK = "task"
    PLANNING = "planning"
    ANALYSIS = "analysis"
    EXECUTION = "execution"
    SEARCH = "search"
    PORTFOLIO = "portfolio"
    QUANT = "quant"
    GROWTH = "growth"
    PROJECTS = "projects"
    GENERAL = "general"


class ModelProfile(str, Enum):
    FAST = "fast"
    BALANCED = "balanced"
    REASONING = "reasoning"
    LONG_CONTEXT = "long_context"
    VISION_READY = "vision_ready"
    TOOL_READY = "tool_ready"
    JSON_READY = "json_ready"


class PlanMode(str, Enum):
    SEQUENTIAL = "sequential"
    PARALLEL_ABSTRACTION = "parallel_abstraction"
    CONDITIONAL = "conditional"


class OrchestrationStage(str, Enum):
    USER_REQUEST = "user_request"
    INTENT_DETECTION = "intent_detection"
    AGENT_SELECTION = "agent_selection"
    PROVIDER_SELECTION = "provider_selection"
    MODEL_SELECTION = "model_selection"
    CONTEXT_BUILDER = "context_builder"
    MEMORY_RETRIEVAL = "memory_retrieval"
    PLANNING = "planning"
    TOOL_SELECTION = "tool_selection"
    INTEGRATION_SELECTION = "integration_selection"
    APPROVAL_EVALUATION = "approval_evaluation"
    EXECUTION = "execution"
    RESPONSE_ASSEMBLY = "response_assembly"
    STREAMING = "streaming"
    LOGGING = "logging"
    TELEMETRY = "telemetry"
    AUDIT = "audit"


class RoutingStrategy(str, Enum):
    DETERMINISTIC_INTENT = "deterministic_intent"
    SCORED_AGENT = "scored_agent"
    CAPABILITY_PROVIDER = "capability_provider"
    PROFILE_MODEL = "profile_model"


class ExecutionPolicy(str, Enum):
    SEQUENTIAL = "sequential"
    PARALLEL_ABSTRACTION = "parallel_abstraction"
    CONDITIONAL = "conditional"
    APPROVAL_CHECKPOINT = "approval_checkpoint"
    RETRY_CHECKPOINT = "retry_checkpoint"


class PlanTarget(str, Enum):
    TOOL = "tool"
    INTEGRATION = "integration"
    BACKGROUND = "background"
    AGENT = "agent"
    RESPONSE = "response"


class StreamEventType(str, Enum):
    CHUNK = "chunk"
    TOOL_UPDATE = "tool_update"
    PROGRESS = "progress"
    FINAL = "final"


class OrchestrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=100000)
    requested_intent: OrchestrationIntent | None = None
    model_profile: ModelProfile = ModelProfile.BALANCED
    require_json: bool = False
    has_images: bool = False
    stream: bool = False
    max_context_tokens: int = Field(default=16000, ge=1)
    max_output_tokens: int = Field(default=1024, ge=1)
    budget_gbp: Decimal = Field(default=Decimal("0"), ge=0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AgentCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    agent_id: UUID
    name: str
    capabilities: frozenset[OrchestrationIntent]
    priority: int = Field(default=0, ge=0)
    available: bool = True
    healthy: bool = True
    estimated_cost: Decimal = Field(default=Decimal("0"), ge=0)
    permission_granted: bool = True


class RouteSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: UUID
    provider: str
    model: str
    model_profile: ModelProfile
    rationale: list[str]


class PlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    target: PlanTarget
    name: str
    operation: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    condition: str | None = None
    approval_checkpoint: bool = False
    retry_checkpoint: bool = True


class ExecutionPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent: OrchestrationIntent
    mode: PlanMode
    steps: list[PlanStep]
    policies: frozenset[ExecutionPolicy]
    created_at: datetime


class ApprovalRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: UUID
    target: PlanTarget
    policy: str
    reason: str
    approved: bool = False


class OrchestrationCost(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_cost_gbp: Decimal = Field(ge=0)
    tool_cost_gbp: Decimal = Field(ge=0)
    integration_cost_gbp: Decimal = Field(ge=0)
    execution_cost_gbp: Decimal = Field(ge=0)
    total_cost_gbp: Decimal = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    within_budget: bool


class ExecutedAction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: UUID
    target: PlanTarget
    name: str
    success: bool
    output: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class OrchestrationChunk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: StreamEventType
    index: int = Field(ge=0)
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    final: bool = False


class TelemetryRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    correlation_id: UUID
    latency_ms: float = Field(ge=0)
    provider: str
    agent_id: UUID
    model: str
    tool_count: int = Field(ge=0)
    integration_count: int = Field(ge=0)
    retries: int = Field(ge=0)
    approval_count: int = Field(ge=0)
    failure_count: int = Field(ge=0)
    success: bool
    timestamp: datetime


class ComponentHealth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    component: str
    available: bool
    healthy: bool
    checked_at: datetime
    detail: str | None = None


class OrchestrationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    correlation_id: UUID
    intent: OrchestrationIntent
    route: RouteSelection
    plan: ExecutionPlan
    executed_tools: list[ExecutedAction]
    executed_integrations: list[ExecutedAction]
    executed_jobs: list[ExecutedAction]
    approvals: list[ApprovalRequirement]
    costs: OrchestrationCost
    latency_ms: float = Field(ge=0)
    warnings: list[str] = Field(default_factory=list)
    failures: list[str] = Field(default_factory=list)
    final_response: str
    chunks: list[OrchestrationChunk] = Field(default_factory=list)
