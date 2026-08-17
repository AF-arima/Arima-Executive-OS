import asyncio
from uuid import uuid4

from app.services.tool_execution import ToolExecutionService
from app.tools.factory import ToolFactory
from app.tools.schemas import ToolExecutionRequest, ToolHealthStatus
from tests.database.helpers import sqlite_session
from tests.tools.helpers import make_context


def test_every_internal_tool_exposes_contract_and_executes_structured() -> None:
    async def scenario() -> None:
        async with sqlite_session() as session:
            context = await make_context(session)
            registry = ToolFactory(session).create_registry()
            service = ToolExecutionService(session, registry)
            identifier = {"id": str(uuid4())}
            payloads = {
                "project.summary": identifier,
                "task.summary": identifier,
                "lead.summary": identifier,
                "memory.store": {
                    "key": "milestone.4b",
                    "value": "Internal tool framework",
                },
            }
            results = []
            for tool in registry.all():
                metadata = tool.metadata()
                assert metadata.name == tool.tool_name()
                assert metadata.input_schema["type"] == "object"
                assert (await tool.health()).status is ToolHealthStatus.HEALTHY
                result = await service.execute(
                    ToolExecutionRequest(
                        tool_name=tool.tool_name(),
                        payload=payloads.get(tool.tool_name(), {}),
                    ),
                    context,
                )
                results.append(result)
            assert len(results) == 26
            assert all(result.tool_version == "1.0.0" for result in results)

    asyncio.run(scenario())
