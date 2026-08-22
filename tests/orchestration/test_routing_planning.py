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
        "Explain what factors affect Bitcoin's price.": OrchestrationIntent.GENERAL_ASSET_DISCUSSION,
        "What are the main financial news stories today?": OrchestrationIntent.CURRENT_NEWS,
    }
    assert {
        engine.detect(OrchestrationRequest(content=text))
        for text in cases
    } == set(cases.values())


def test_asset_discussion_does_not_invoke_market_tool() -> None:
    planner = OrchestrationPlanner()
    for content in (
        "Explain what factors affect Bitcoin's price.",
        "Why is Bitcoin's price volatile?",
        "چه عواملی روی قیمت بیت‌کوین تأثیر می‌گذارند؟",
    ):
        plan = planner.plan(IntentEngine().detect(OrchestrationRequest(content=content)), content)
        assert not any(step.name == "market.current_price" for step in plan.steps)


def test_live_market_requests_invoke_market_tool_in_both_languages() -> None:
    planner = OrchestrationPlanner()
    for content in (
        "What is the current BTC price?",
        "How much is BTC trading at right now?",
        "قیمت فعلی بیت‌کوین چنده؟",
    ):
        plan = planner.plan(IntentEngine().detect(OrchestrationRequest(content=content)), content)
        market_step = next(step for step in plan.steps if step.name == "market.current_price")
        assert market_step.payload == {"instrument": "BTCUSD"}


def test_current_news_is_explicitly_guarded_without_a_news_provider() -> None:
    request = OrchestrationRequest(content="What are the main financial news stories today?")
    assert IntentEngine().detect(request) is OrchestrationIntent.CURRENT_NEWS
    plan = OrchestrationPlanner().plan(OrchestrationIntent.CURRENT_NEWS, request.content)
    assert not any(step.name == "market.current_price" for step in plan.steps)


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
