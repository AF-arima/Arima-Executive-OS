import asyncio
from decimal import Decimal
from uuid import uuid4

from app.orchestration.optimizer import OrchestrationOptimizer
from app.orchestration.planner import OrchestrationPlanner
from app.orchestration.router import (
    AgentRouter,
    IntentEngine,
    ModelRouter,
    ProviderRouter,
)
from app.orchestration.schemas import (
    AgentCandidate,
    ExecutionPolicy,
    ModelProfile,
    OrchestrationIntent,
    OrchestrationRequest,
    PlanMode,
    PlanTarget,
)
from app.providers.factory import ProviderFactory


def test_deterministic_intent_routing() -> None:
    engine = IntentEngine()
    cases = {
        "Review my portfolio": OrchestrationIntent.PORTFOLIO,
        "Run a quant backtest": OrchestrationIntent.QUANT,
        "Search for market news": OrchestrationIntent.SEARCH,
        "Show project milestones": OrchestrationIntent.PROJECTS,
        "Make a roadmap plan": OrchestrationIntent.PLANNING,
        "Hello there": OrchestrationIntent.GENERAL,
    }
    assert {
        engine.detect(OrchestrationRequest(content=text))
        for text in cases
    } == set(cases.values())


def test_agent_provider_model_routing() -> None:
    intent = OrchestrationIntent.QUANT
    candidates = (
        AgentCandidate(
            agent_id=uuid4(),
            name="General",
            capabilities=frozenset({OrchestrationIntent.GENERAL}),
            priority=50,
        ),
        AgentCandidate(
            agent_id=uuid4(),
            name="Quant",
            capabilities=frozenset({intent}),
            priority=10,
            estimated_cost=Decimal("0"),
        ),
    )
    assert AgentRouter().select(candidates, intent).name == "Quant"

    async def provider_scenario() -> None:
        provider = await ProviderRouter(
            ProviderFactory().build_registry()
        ).select(frozenset())
        assert provider.provider.value == "mock"
        assert ModelRouter().select(
            provider, ModelProfile.BALANCED
        ) in provider.models

    asyncio.run(provider_scenario())


def test_planning_and_optimisation_policies() -> None:
    planner = OrchestrationPlanner()
    project = planner.plan(OrchestrationIntent.PROJECTS, "projects")
    assert project.steps[0].target is PlanTarget.TOOL
    search = planner.plan(OrchestrationIntent.SEARCH, "search this")
    assert search.steps[0].target is PlanTarget.INTEGRATION
    assert len(project.policies) == len(ExecutionPolicy)
    assert (
        planner.plan(
            OrchestrationIntent.PROJECTS,
            "projects",
            mode=PlanMode.PARALLEL_ABSTRACTION,
        ).mode
        is PlanMode.PARALLEL_ABSTRACTION
    )
    optimizer = OrchestrationOptimizer()
    assert (
        optimizer.model_profile(
            OrchestrationRequest(content="analyse"),
            OrchestrationIntent.ANALYSIS,
        )
        is ModelProfile.REASONING
    )
