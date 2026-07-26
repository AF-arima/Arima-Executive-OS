import asyncio

from sqlalchemy import func, select

from app.database.models import AuditLog
from app.orchestration.factory import OrchestrationFactory
from app.orchestration.schemas import (
    OrchestrationIntent,
    OrchestrationRequest,
    OrchestrationStage,
)
from app.orchestration.telemetry import InMemoryTelemetrySink
from tests.database.helpers import sqlite_session
from tests.orchestration.helpers import make_context


def test_factory_registers_all_pipeline_stages() -> None:
    async def scenario() -> None:
        async with sqlite_session() as session:
            factory = OrchestrationFactory(session)
            engine = factory.create()
            registry = factory.registry(engine)
            assert len(registry) == 17
            assert set(registry.stages()) == set(OrchestrationStage)

    asyncio.run(scenario())


def test_end_to_end_general_pipeline_streaming_telemetry_and_audit() -> None:
    async def scenario() -> None:
        async with sqlite_session() as session:
            sink = InMemoryTelemetrySink()
            engine = OrchestrationFactory(
                session, telemetry_sink=sink
            ).create()
            context = await make_context(
                session,
                OrchestrationRequest(
                    content="Hello executive assistant",
                    stream=True,
                ),
            )
            result = await engine.execute(context)
            assert result.intent is OrchestrationIntent.GENERAL
            assert result.route.provider == "mock"
            assert result.final_response.startswith("Mock response:")
            assert result.chunks[-1].final
            assert len(sink.records()) == 1
            assert await session.scalar(
                select(func.count()).select_from(AuditLog)
            ) == 1

    asyncio.run(scenario())


def test_pipeline_delegates_tools_integrations_and_background_jobs() -> None:
    async def scenario() -> None:
        intents = (
            ("Show project status", OrchestrationIntent.PROJECTS),
            ("Search for trends", OrchestrationIntent.SEARCH),
            ("Run quant research", OrchestrationIntent.QUANT),
        )
        for content, expected in intents:
            async with sqlite_session() as session:
                engine = OrchestrationFactory(session).create()
                context = await make_context(
                    session, OrchestrationRequest(content=content)
                )
                result = await engine.execute(context)
                assert result.intent is expected
                assert (
                    result.executed_tools
                    or result.executed_integrations
                    or result.executed_jobs
                )

    asyncio.run(scenario())
