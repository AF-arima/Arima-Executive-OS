from __future__ import annotations

from decimal import Decimal

from app.orchestration.exceptions import RoutingError
from app.orchestration.health import HealthContract
from app.orchestration.schemas import (
    AgentCandidate,
    ModelProfile,
    OrchestrationIntent,
    OrchestrationRequest,
)
from app.providers.base import ProviderAdapter
from app.providers.registry import ProviderRegistry
from app.providers.types import ProviderCapability


class IntentEngine(HealthContract):
    component_name = "intent"

    KEYWORDS = {
        OrchestrationIntent.PORTFOLIO: ("portfolio", "holdings"),
        OrchestrationIntent.QUANT: ("quant", "backtest", "signal"),
        OrchestrationIntent.GROWTH: ("growth", "content", "campaign"),
        OrchestrationIntent.PROJECTS: ("project", "milestone"),
        OrchestrationIntent.SEARCH: ("search", "find", "look up"),
        OrchestrationIntent.PLANNING: ("plan", "roadmap"),
        OrchestrationIntent.ANALYSIS: ("analyse", "analyze", "compare"),
        OrchestrationIntent.EXECUTION: ("execute", "run", "do"),
        OrchestrationIntent.TASK: ("task", "todo"),
        OrchestrationIntent.CONVERSATION: ("chat", "discuss"),
    }

    def detect(self, request: OrchestrationRequest) -> OrchestrationIntent:
        if request.requested_intent is not None:
            return request.requested_intent
        content = request.content.lower()
        for intent, keywords in self.KEYWORDS.items():
            if any(keyword in content for keyword in keywords):
                return intent
        return OrchestrationIntent.GENERAL


class AgentRouter(HealthContract):
    component_name = "agent_router"

    def select(
        self,
        candidates: tuple[AgentCandidate, ...],
        intent: OrchestrationIntent,
    ) -> AgentCandidate:
        eligible = [
            candidate
            for candidate in candidates
            if candidate.available
            and candidate.healthy
            and candidate.permission_granted
        ]
        if not eligible:
            raise RoutingError("No eligible agent is available")

        def score(candidate: AgentCandidate) -> tuple[int, Decimal, str]:
            capability_score = 100 if intent in candidate.capabilities else 0
            return (
                capability_score + candidate.priority,
                -candidate.estimated_cost,
                candidate.name,
            )

        return max(eligible, key=score)


class ProviderRouter(HealthContract):
    component_name = "provider_router"

    def __init__(self, registry: ProviderRegistry) -> None:
        self.registry = registry

    async def select(
        self,
        required: frozenset[ProviderCapability],
    ) -> ProviderAdapter:
        candidates = self.registry.find(capabilities=required)
        healthy: list[tuple[int, str, ProviderAdapter]] = []
        for adapter in candidates:
            status = await adapter.health()
            if status.available:
                healthy.append(
                    (
                        status.latency_ms or 0,
                        adapter.provider.value,
                        adapter,
                    )
                )
        if not healthy:
            raise RoutingError("No healthy provider matches capabilities")
        return min(healthy, key=lambda item: (item[0], item[1]))[2]


class ModelRouter(HealthContract):
    component_name = "model_router"

    def select(
        self,
        provider: ProviderAdapter,
        profile: ModelProfile,
    ) -> str:
        requirements = {
            ModelProfile.VISION_READY: lambda model: provider.supports_images(
                model
            ),
            ModelProfile.TOOL_READY: lambda model: provider.supports_tools(
                model
            ),
            ModelProfile.JSON_READY: lambda model: (
                provider.supports_json_mode(model)
            ),
        }
        predicate = requirements.get(profile, lambda model: True)
        for model in provider.models:
            if predicate(model):
                return model
        raise RoutingError(
            f"No model matches orchestration profile {profile.value}"
        )
