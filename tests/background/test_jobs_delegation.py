import asyncio
from dataclasses import replace
from types import SimpleNamespace
from typing import cast
from uuid import uuid4

from sqlalchemy import func, select

from app.background.clock import FixedClock
from app.background.factory import BackgroundJobFactory
from app.background.runner import BackgroundJobRunner
from app.background.schemas import BackgroundExecutionRequest
from app.database.models import AgentToolExecution
from app.services.agent_execution import ExecutionOrchestrator
from app.services.background_execution import BackgroundExecutionService
from tests.background.helpers import FIXED_NOW, make_context
from tests.database.helpers import sqlite_session


class FakeAgentExecution:
    def __init__(self) -> None:
        self.calls = 0

    async def execute_queued(
        self, run_id, actor, *, provider_name="mock"
    ):
        self.calls += 1
        return SimpleNamespace(run_id=run_id, provider_name=provider_name)


def test_every_mock_job_metadata_health_and_dry_run() -> None:
    async def scenario() -> None:
        async with sqlite_session() as session:
            clock = FixedClock(FIXED_NOW)
            registry = BackgroundJobFactory(
                clock=clock
            ).build_registry()
            base_job = registry.get("quant_research_summary")
            context = await make_context(session, base_job)
            runner = BackgroundJobRunner(
                session, registry, clock=clock
            )
            results = []
            for job in registry.all():
                assert job.metadata().name == job.job_name()
                assert (await job.health()).available
                job_context = replace(
                    context, job=job, correlation_id=uuid4()
                )
                results.append(
                    await runner.run(
                        BackgroundExecutionRequest(
                            job_name=job.job_name(), dry_run=True
                        ),
                        job_context,
                    )
                )
            assert len(results) == 12
            assert all(result.success for result in results)

    asyncio.run(scenario())


def test_internal_tool_and_integration_delegation() -> None:
    async def scenario() -> None:
        async with sqlite_session() as session:
            clock = FixedClock(FIXED_NOW)
            registry = BackgroundJobFactory(
                clock=clock
            ).build_registry()
            tool_job = registry.get("platform_health_review")
            context = await make_context(session, tool_job)
            service = BackgroundExecutionService(
                session, clock=clock, registry=registry
            )
            tool_result = await service.execute_job_now(
                BackgroundExecutionRequest(job_name=tool_job.job_name()),
                context,
            )
            assert tool_result.metadata["success"] is True
            integration_job = registry.get("integration_health_review")
            integration_result = await service.execute_job_now(
                BackgroundExecutionRequest(
                    job_name=integration_job.job_name()
                ),
                replace(
                    context,
                    job=integration_job,
                    correlation_id=uuid4(),
                ),
            )
            assert integration_result.metadata["success"] is True
            assert await session.scalar(
                select(func.count()).select_from(AgentToolExecution)
            ) == 2

    asyncio.run(scenario())


def test_agent_execution_delegation() -> None:
    async def scenario() -> None:
        async with sqlite_session() as session:
            clock = FixedClock(FIXED_NOW)
            registry = BackgroundJobFactory(
                clock=clock
            ).build_registry()
            job = registry.get("executive_briefing")
            context = await make_context(session, job)
            fake = FakeAgentExecution()
            runner = BackgroundJobRunner(
                session,
                registry,
                clock=clock,
                agent_service=cast(ExecutionOrchestrator, fake),
            )
            result = await runner.run(
                BackgroundExecutionRequest(job_name=job.job_name()),
                context,
            )
            assert result.success
            assert result.metadata["delegated"] is True
            assert fake.calls == 1

    asyncio.run(scenario())
