import asyncio

import pytest
from sqlalchemy import func, select

from app.database.models import (
    AgentToolDefinition,
    AgentToolExecution,
    AuditLog,
)
from app.services.tool_execution import ToolExecutionService
from app.tools.exceptions import ToolPermissionDeniedError
from app.tools.factory import ToolFactory
from app.tools.logging import InMemoryToolExecutionLog
from app.tools.schemas import (
    PermissionOutcome,
    ToolExecutionRequest,
    ToolPermission,
)
from tests.database.helpers import sqlite_session
from tests.tools.helpers import make_context


def test_execution_is_structured_audited_and_logged() -> None:
    async def scenario() -> None:
        async with sqlite_session() as session:
            context = await make_context(session)
            log = InMemoryToolExecutionLog()
            service = ToolExecutionService(
                session,
                ToolFactory(session).create_registry(),
                log_sink=log,
            )
            result = await service.execute(
                ToolExecutionRequest(tool_name="platform.health"),
                context,
            )
            assert result.success
            assert result.data["status"] == "healthy"
            assert result.tool_version == "1.0.0"
            assert result.correlation_id == context.correlation_id
            assert len(log.records()) == 1
            assert (
                log.records()[0].permission_outcome
                is PermissionOutcome.ALLOWED
            )
            assert await session.scalar(
                select(func.count()).select_from(AuditLog)
            ) == 1

            batch = await service.execute_parallel(
                [
                    ToolExecutionRequest(tool_name="platform.health"),
                    ToolExecutionRequest(tool_name="system.status"),
                ],
                context,
            )
            assert batch.execution_mode == "parallel_abstraction"
            assert len(batch.results) == 2

            failed = await service.execute(
                ToolExecutionRequest(
                    tool_name="memory.store",
                    payload={"key": "", "value": "private-tool-value"},
                ),
                context,
            )
            assert failed.success is False
            assert failed.failure == (
                "Tool execution failed (ToolValidationError)"
            )
            assert "private-tool-value" not in failed.failure

    asyncio.run(scenario())


def test_default_logger_reuses_agent_platform_persistence() -> None:
    async def scenario() -> None:
        async with sqlite_session() as session:
            context = await make_context(session)
            service = ToolExecutionService(
                session, ToolFactory(session).create_registry()
            )
            await service.execute(
                ToolExecutionRequest(tool_name="platform.health"),
                context,
            )
            assert await session.scalar(
                select(func.count()).select_from(AgentToolDefinition)
            ) == 1
            assert await session.scalar(
                select(func.count()).select_from(AgentToolExecution)
            ) == 1

    asyncio.run(scenario())


def test_denied_execution_is_rejected_audited_and_logged() -> None:
    async def scenario() -> None:
        async with sqlite_session() as session:
            context = await make_context(
                session,
                role_name="viewer",
                permissions=frozenset({ToolPermission.READ}),
            )
            log = InMemoryToolExecutionLog()
            service = ToolExecutionService(
                session,
                ToolFactory(session).create_registry(),
                log_sink=log,
            )
            with pytest.raises(ToolPermissionDeniedError):
                await service.execute(
                    ToolExecutionRequest(
                        tool_name="memory.store",
                        payload={
                            "key": "denied",
                            "value": "must not be stored",
                        },
                    ),
                    context,
                )
            assert len(log.records()) == 1
            assert (
                log.records()[0].permission_outcome
                is PermissionOutcome.DENIED
            )
            assert await session.scalar(
                select(func.count()).select_from(AuditLog)
            ) == 1

    asyncio.run(scenario())
