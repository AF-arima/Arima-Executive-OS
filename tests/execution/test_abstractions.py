import asyncio
from decimal import Decimal
from uuid import uuid4

import pytest

from app.execution import (
    CostEstimator,
    ExecutionContext,
    ExecutionTimeout,
    MockProviderAdapter,
    MockTokenEstimator,
    Pricing,
    PromptBuildFailure,
    PromptBuilder,
    PromptMessage,
    ProviderFailure,
    ProviderRegistry,
    ProviderRequest,
    RetryExhausted,
    RetryExecutor,
    RetryPolicy,
    StaticPricingStrategy,
    StructuredPrompt,
    TimeoutPolicy,
    ToolAdapterRegistry,
    mock_tool_adapters,
)
from app.execution.exceptions import ExecutionError
from app.execution.tool_adapters import MOCK_TOOL_SLUGS
from app.services.exceptions import ServiceError


def execution_context() -> ExecutionContext:
    run_id = uuid4()
    return ExecutionContext(
        run_id=run_id,
        snapshot_id=uuid4(),
        user={"id": str(uuid4()), "roles": ["analyst"]},
        conversation={"id": str(uuid4()), "title": "Plan"},
        messages=(
            {
                "role": "user",
                "content": "Summarise the plan",
                "sequence_number": 1,
            },
        ),
        memory=({"value": "Use concise bullets", "importance": 5},),
        permissions={"invoke_agents": True},
        projects=(),
        tasks=(),
        crm={"companies": [], "total": 0},
        outreach={"drafts": [], "total": 0},
        notifications=(),
        previous_runs=(),
    )


def test_prompt_token_and_cost_estimators_are_deterministic() -> None:
    context = execution_context()
    prompt = PromptBuilder().build(
        system_instructions="Be concise.",
        context=context,
    )
    assert prompt.conversation == (
        PromptMessage(role="user", content="Summarise the plan"),
    )
    assert prompt.memory == ("Use concise bullets",)
    estimator = MockTokenEstimator()
    assert estimator.estimate_prompt(prompt) == estimator.estimate_prompt(
        prompt
    )
    assert estimator.estimate_text("") == 0

    cost = CostEstimator(
        StaticPricingStrategy(
            Pricing(
                prompt_per_thousand_gbp=Decimal("2.00"),
                completion_per_thousand_gbp=Decimal("4.00"),
            )
        )
    ).estimate(
        provider_name="mock",
        model_name=None,
        prompt_tokens=500,
        completion_tokens=250,
    )
    assert cost.prompt_cost_gbp == Decimal("1.000000")
    assert cost.completion_cost_gbp == Decimal("1.000000")
    assert cost.total_cost_gbp == Decimal("2.000000")

    with pytest.raises(PromptBuildFailure):
        PromptBuilder().build(
            system_instructions=" ",
            context=context,
        )


def test_retry_and_timeout_policies_do_not_sleep() -> None:
    attempts: list[int] = []

    async def flaky(attempt: int) -> str:
        attempts.append(attempt)
        if attempt < 3:
            raise ProviderFailure("retry", retryable=True)
        return "ok"

    policy = RetryPolicy(
        max_attempts=3,
        backoff_base_ms=50,
        backoff_factor=2,
    )
    assert asyncio.run(RetryExecutor(policy).run(flaky)) == "ok"
    assert attempts == [1, 2, 3]
    assert policy.backoff_metadata(3) == {
        "attempt": 3,
        "max_attempts": 3,
        "backoff_ms": 200,
        "sleep_performed": False,
    }

    async def exhausted(attempt: int) -> str:
        del attempt
        raise ProviderFailure("still unavailable", retryable=True)

    with pytest.raises(RetryExhausted) as caught:
        asyncio.run(
            RetryExecutor(RetryPolicy(max_attempts=2)).run(exhausted)
        )
    assert caught.value.attempts == 2

    timeout = TimeoutPolicy(max_duration_ms=100)
    timeout.ensure_within_limit(100)
    with pytest.raises(ExecutionTimeout):
        timeout.ensure_within_limit(101)
    assert timeout.metadata()["timer_active"] is False


def test_provider_protocol_registry_and_mock_are_deterministic() -> None:
    adapter = MockProviderAdapter()
    registry = ProviderRegistry((adapter,))
    prompt = StructuredPrompt(
        system_instructions="Assist.",
        conversation=(PromptMessage(role="user", content="Hello"),),
        memory=(),
        tool_outputs=(),
        context={},
    )
    request = ProviderRequest(run_id=uuid4(), prompt=prompt)

    async def execute_twice() -> tuple[str, str]:
        selected = registry.get("mock")
        assert (await selected.health()).available is True
        prepared = await selected.prepare(request)
        first = await selected.execute(prepared)
        second = await selected.execute(prepared)
        await selected.cancel(request.run_id)
        return first.content, second.content

    first, second = asyncio.run(execute_twice())
    assert first == second == "Mock response: Hello"
    assert request.run_id in adapter.cancelled_runs
    assert adapter.estimate_cost(100, 50) == Decimal("0")


def test_all_mock_tool_adapters_validate_execute_cancel_and_describe() -> None:
    context = execution_context()
    adapters = mock_tool_adapters()
    assert {adapter.slug for adapter in adapters} == set(MOCK_TOOL_SLUGS)
    registry = ToolAdapterRegistry(adapters)

    async def exercise() -> None:
        for slug in MOCK_TOOL_SLUGS:
            adapter = registry.get(slug)
            adapter.validate({"query": "deterministic"}, context)
            first = await adapter.execute({"query": "deterministic"}, context)
            second = await adapter.execute({"query": "deterministic"}, context)
            assert first == second
            assert first["mutated"] is False
            assert adapter.metadata()["external_integration"] is False
            await adapter.cancel(uuid4())

    asyncio.run(exercise())


def test_execution_errors_reuse_service_error_hierarchy() -> None:
    assert issubclass(ExecutionError, ServiceError)
    assert issubclass(ProviderFailure, ExecutionError)
