from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any


class ProviderName(str, Enum):
    MOCK = "mock"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"
    NVIDIA = "nvidia"
    OLLAMA = "ollama"


class ProviderStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class ProviderCapability(str, Enum):
    STREAMING = "streaming"
    VISION = "vision"
    JSON_MODE = "json_mode"
    TOOLS = "tools"
    REASONING = "reasoning"
    EMBEDDINGS = "embeddings"
    MULTIMODAL = "multimodal"


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    streaming: bool = False
    vision: bool = False
    json_mode: bool = False
    tools: bool = False
    reasoning: bool = False
    embeddings: bool = False
    multimodal: bool = False

    def enabled(self) -> frozenset[ProviderCapability]:
        return frozenset(
            capability
            for capability in ProviderCapability
            if bool(getattr(self, capability.value))
        )

    def supports(
        self,
        required: frozenset[ProviderCapability],
    ) -> bool:
        return required.issubset(self.enabled())


@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int
    output_tokens: int

    def __post_init__(self) -> None:
        if self.input_tokens < 0 or self.output_tokens < 0:
            raise ValueError("Token usage cannot be negative")

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True, slots=True)
class EstimatedCost:
    input_cost: Decimal
    output_cost: Decimal
    total_cost: Decimal
    currency: str = "GBP"

    def __post_init__(self) -> None:
        if min(self.input_cost, self.output_cost, self.total_cost) < 0:
            raise ValueError("Estimated cost cannot be negative")
        if self.total_cost != self.input_cost + self.output_cost:
            raise ValueError("Total cost must equal input plus output cost")


@dataclass(frozen=True, slots=True)
class PricingInfo:
    input_per_million_tokens: Decimal | None = None
    output_per_million_tokens: Decimal | None = None
    currency: str = "GBP"
    source: str = "not_configured"

    @property
    def configured(self) -> bool:
        return (
            self.input_per_million_tokens is not None
            and self.output_per_million_tokens is not None
        )


@dataclass(frozen=True, slots=True)
class ModelInfo:
    provider: ProviderName
    model: str
    display_name: str
    context_window: int
    max_output_tokens: int
    capabilities: ProviderCapabilities
    pricing: PricingInfo = field(default_factory=PricingInfo)

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise ValueError("Model identifier is required")
        if self.context_window < 1 or self.max_output_tokens < 1:
            raise ValueError("Model token limits must be positive")
        if self.max_output_tokens > self.context_window:
            raise ValueError(
                "Model output limit cannot exceed its context window"
            )


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    provider: ProviderName
    status: ProviderStatus
    available: bool
    latency_ms: int | None
    checked_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    message: str | None = None

    def __post_init__(self) -> None:
        if self.latency_ms is not None and self.latency_ms < 0:
            raise ValueError("Provider latency cannot be negative")
        if self.checked_at.tzinfo is None:
            raise ValueError("Provider health timestamp must be timezone-aware")


class MessageRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


@dataclass(frozen=True, slots=True)
class ProviderMessage:
    role: MessageRole
    content: str
    images: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.content and not self.images:
            raise ValueError("Provider message requires content or images")


@dataclass(frozen=True, slots=True)
class CompletionRequest:
    model: str
    messages: tuple[ProviderMessage, ...]
    temperature: float = 0.2
    max_output_tokens: int = 1_024
    tools: tuple[dict[str, Any], ...] = ()
    json_mode: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise ValueError("Completion model is required")
        if not self.messages:
            raise ValueError("At least one provider message is required")
        if not 0 <= self.temperature <= 2:
            raise ValueError("Temperature must be between zero and two")
        if self.max_output_tokens < 1:
            raise ValueError("Output token limit must be positive")


@dataclass(frozen=True, slots=True)
class CompletionResponse:
    provider: ProviderName
    model: str
    content: str
    usage: TokenUsage
    estimated_cost: EstimatedCost
    finish_reason: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class StreamChunk:
    provider: ProviderName
    model: str
    index: int
    content: str
    finished: bool = False


@dataclass(frozen=True, slots=True)
class EmbeddingRequest:
    model: str
    inputs: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise ValueError("Embedding model is required")
        if not self.inputs or any(not item for item in self.inputs):
            raise ValueError("Embedding inputs cannot be empty")


@dataclass(frozen=True, slots=True)
class EmbeddingResponse:
    provider: ProviderName
    model: str
    vectors: tuple[tuple[float, ...], ...]
    usage: TokenUsage
