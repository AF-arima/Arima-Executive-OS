import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.background.factory import BackgroundJobFactory
from app.integrations.factory import ConnectorFactory
from app.orchestration.approval import OrchestrationApprovalEngine
from app.orchestration.cost import OrchestrationCostEngine
from app.orchestration.exceptions import (
    OrchestrationApprovalRequired,
    OrchestrationBudgetExceeded,
)
from app.orchestration.fallback import OrchestrationFallback
from app.orchestration.policy import OrchestrationPolicy
from app.orchestration.schemas import (
    ExecutionPlan,
    OrchestrationCost,
    OrchestrationIntent,
    PlanMode,
    PlanStep,
    PlanTarget,
)
from app.providers.factory import ProviderFactory


def test_approval_evaluation_and_checkpoint() -> None:
    engine = OrchestrationApprovalEngine(
        ConnectorFactory().build_registry(),
        BackgroundJobFactory().build_registry(),
    )
    plan = ExecutionPlan(
        intent=OrchestrationIntent.EXECUTION,
        mode=PlanMode.SEQUENTIAL,
        steps=[
            PlanStep(
                target=PlanTarget.INTEGRATION,
                name="slack",
                operation="send_message",
            )
        ],
        policies=frozenset(),
        created_at=datetime.now(timezone.utc),
    )
    requirements = engine.evaluate(plan)
    assert requirements[0].policy == "user"
    with pytest.raises(OrchestrationApprovalRequired):
        engine.require(requirements)
    approved = engine.evaluate(
        plan, approved_steps=frozenset({str(plan.steps[0].id)})
    )
    engine.require(approved)


def test_fallback_retry_and_graceful_degradation() -> None:
    async def scenario() -> None:
        attempts = 0

        async def flaky() -> str:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("retry")
            return "ok"

        fallback = OrchestrationFallback(
            OrchestrationPolicy(maximum_retries=2)
        )
        result, retries = await fallback.retry(flaky)
        assert result == "ok"
        assert retries == 1
        degraded = fallback.graceful(RuntimeError("private-provider-detail"))
        assert degraded["degraded"] is True
        assert degraded["message"] == (
            "Orchestration action failed (RuntimeError)"
        )
        assert "private-provider-detail" not in str(degraded)
        assert (
            fallback.model_fallback(
                "missing", ("mock-model",), frozenset({"mock-model"})
            )
            == "mock-model"
        )
        assert (
            fallback.tool_fallback(
                "missing", ("platform.health",), frozenset({"platform.health"})
            )
            == "platform.health"
        )

    asyncio.run(scenario())


def test_fallback_deadline_stops_retries_and_propagates_cancellation() -> None:
    async def scenario() -> None:
        fallback = OrchestrationFallback(OrchestrationPolicy(maximum_retries=2))
        attempts = 0
        started = asyncio.Event()

        async def blocking() -> str:
            nonlocal attempts
            attempts += 1
            started.set()
            await asyncio.Event().wait()
            return "unreachable"

        task = asyncio.create_task(
            fallback.retry(
                blocking,
                deadline=asyncio.get_running_loop().time() + 10,
            )
        )
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert attempts == 1

        attempts = 0
        with pytest.raises(asyncio.TimeoutError):
            await fallback.retry(
                blocking,
                deadline=asyncio.get_running_loop().time() + 0.001,
            )
        assert attempts == 1

    asyncio.run(scenario())


def test_cost_estimation_and_budget_enforcement() -> None:
    provider = ProviderFactory().create(provider="mock")
    cost = OrchestrationCostEngine().estimate(
        provider,
        model=provider.models[0],
        input_tokens=10,
        output_tokens=5,
        tool_count=2,
        integration_count=1,
        budget_gbp=Decimal("1"),
    )
    assert cost.total_cost_gbp == Decimal("0")
    OrchestrationCostEngine.require_budget(cost)
    blocked = OrchestrationCost(
        provider_cost_gbp=Decimal("2"),
        tool_cost_gbp=Decimal("0"),
        integration_cost_gbp=Decimal("0"),
        execution_cost_gbp=Decimal("0"),
        total_cost_gbp=Decimal("2"),
        input_tokens=1,
        output_tokens=1,
        within_budget=False,
    )
    with pytest.raises(OrchestrationBudgetExceeded):
        OrchestrationCostEngine.require_budget(blocked)
