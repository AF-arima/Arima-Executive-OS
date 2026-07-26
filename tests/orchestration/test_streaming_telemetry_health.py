import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from app.orchestration.factory import OrchestrationFactory
from app.orchestration.schemas import (
    StreamEventType,
    TelemetryRecord,
)
from app.orchestration.streaming import OrchestrationStreamer
from app.orchestration.telemetry import (
    InMemoryTelemetrySink,
    OrchestrationTelemetry,
)
from tests.database.helpers import sqlite_session


def test_streaming_chunks_progress_and_final_response() -> None:
    async def scenario() -> None:
        chunks = [
            chunk
            async for chunk in OrchestrationStreamer().stream(
                "hello executive", progress=("planning",)
            )
        ]
        assert chunks[0].event_type is StreamEventType.PROGRESS
        assert chunks[-1].event_type is StreamEventType.FINAL
        assert chunks[-1].final
        assert (
            OrchestrationStreamer.tool_update(
                "project.search", "completed", index=10
            ).event_type
            is StreamEventType.TOOL_UPDATE
        )

    asyncio.run(scenario())


def test_telemetry_sink() -> None:
    async def scenario() -> None:
        sink = InMemoryTelemetrySink()
        telemetry = OrchestrationTelemetry(sink)
        record = TelemetryRecord(
            correlation_id=uuid4(),
            latency_ms=1,
            provider="mock",
            agent_id=uuid4(),
            model="mock-model",
            tool_count=0,
            integration_count=0,
            retries=0,
            approval_count=0,
            failure_count=0,
            success=True,
            timestamp=datetime.now(timezone.utc),
        )
        await telemetry.record(record)
        assert sink.records() == (record,)

    asyncio.run(scenario())


def test_required_component_health_contracts() -> None:
    async def scenario() -> None:
        async with sqlite_session() as session:
            engine = OrchestrationFactory(session).create()
            components = (
                engine.pipeline.planner,
                engine.pipeline.agent_router,
                engine.pipeline.provider_router,
                engine.pipeline.model_router,
                engine.pipeline.executor,
                engine.pipeline.streamer,
                engine.pipeline.approval,
                engine.pipeline.fallback,
                engine.pipeline.cost,
            )
            assert all(
                [(await component.health()).healthy for component in components]
            )

    asyncio.run(scenario())
