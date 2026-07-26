import asyncio

import pytest
from sqlalchemy import func, select

from app.background.clock import FixedClock
from app.background.exceptions import (
    BackgroundCancellationError,
    BackgroundRetryExhaustedError,
    BackgroundTimeoutError,
)
from app.background.factory import BackgroundJobFactory
from app.background.policies import RetryPolicy, TimeoutPolicy
from app.background.runner import BackgroundJobRunner
from app.background.schemas import BackgroundExecutionRequest
from app.database.models import (
    AuditLog,
    BackgroundJobAttempt,
    BackgroundJobEvent,
    BackgroundJobExecution,
)
from tests.background.helpers import FIXED_NOW, make_context
from tests.database.helpers import sqlite_session


def test_runner_state_transitions_logging_audit_and_health() -> None:
    async def scenario() -> None:
        async with sqlite_session() as session:
            registry = BackgroundJobFactory(
                clock=FixedClock(FIXED_NOW)
            ).build_registry()
            job = registry.get("quant_research_summary")
            context = await make_context(session, job)
            runner = BackgroundJobRunner(
                session, registry, clock=FixedClock(FIXED_NOW)
            )
            result = await runner.run(
                BackgroundExecutionRequest(job_name=job.job_name()),
                context,
            )
            assert result.success
            assert await session.scalar(
                select(func.count()).select_from(BackgroundJobExecution)
            ) == 1
            assert await session.scalar(
                select(func.count()).select_from(BackgroundJobAttempt)
            ) == 1
            assert await session.scalar(
                select(func.count()).select_from(BackgroundJobEvent)
            ) == 3
            assert await session.scalar(
                select(func.count()).select_from(AuditLog)
            ) == 3
            assert (await runner.health()).last_success == FIXED_NOW

    asyncio.run(scenario())


def test_cancellation_contract() -> None:
    async def scenario() -> None:
        async with sqlite_session() as session:
            registry = BackgroundJobFactory(
                clock=FixedClock(FIXED_NOW)
            ).build_registry()
            job = registry.get("quant_research_summary")
            context = await make_context(session, job)
            runner = BackgroundJobRunner(
                session, registry, clock=FixedClock(FIXED_NOW)
            )
            runner.cancel(context.correlation_id)
            with pytest.raises(BackgroundCancellationError):
                await runner.run(
                    BackgroundExecutionRequest(job_name=job.job_name()),
                    context,
                )

    asyncio.run(scenario())


def test_retry_and_timeout_policies() -> None:
    policy = RetryPolicy(
        maximum_attempts=3,
        initial_delay_seconds=2,
        maximum_delay_seconds=5,
        backoff_multiplier=2,
    )
    assert policy.delay_for(1) == 2
    assert policy.delay_for(3) == 5
    assert policy.should_retry(RuntimeError(), 1)

    async def scenario() -> None:
        async with sqlite_session() as session:
            registry = BackgroundJobFactory(
                clock=FixedClock(FIXED_NOW)
            ).build_registry()
            job = registry.get("quant_research_summary")
            context = await make_context(session, job)
            runner = BackgroundJobRunner(
                session,
                registry,
                clock=FixedClock(FIXED_NOW),
                timeout_policy=TimeoutPolicy(
                    execution_timeout_seconds=0.000000001
                ),
                retry_policy=RetryPolicy(maximum_attempts=2),
            )
            with pytest.raises(BackgroundRetryExhaustedError) as caught:
                await runner.run(
                    BackgroundExecutionRequest(job_name=job.job_name()),
                    context,
                )
            assert isinstance(caught.value.__cause__, BackgroundTimeoutError)
            assert await session.scalar(
                select(func.count()).select_from(BackgroundJobAttempt)
            ) == 2

    asyncio.run(scenario())
