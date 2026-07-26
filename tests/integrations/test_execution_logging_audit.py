import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select

from app.database.models import (
    AgentToolDefinition,
    AgentToolExecution,
    AuditLog,
)
from app.integrations.exceptions import IntegrationApprovalRequiredError
from app.integrations.factory import ConnectorFactory
from app.integrations.logging import InMemoryIntegrationExecutionLog
from app.integrations.schemas import (
    ApprovalGrant,
    ApprovalOutcome,
    ApprovalPolicy,
    IntegrationRequest,
)
from app.services.integration_execution import IntegrationExecutionService
from tests.database.helpers import sqlite_session
from tests.integrations.helpers import make_context


def test_execution_dry_run_batch_logging_and_audit() -> None:
    async def scenario() -> None:
        async with sqlite_session() as session:
            context = await make_context(session)
            registry = ConnectorFactory().build_registry()
            log = InMemoryIntegrationExecutionLog()
            service = IntegrationExecutionService(
                session, registry, log_sink=log
            )
            result = await service.execute(
                IntegrationRequest(
                    connector="search",
                    operation="web_search",
                    payload={"query": "Arima"},
                    dry_run=True,
                ),
                context,
            )
            assert result.success
            assert result.data["mock"] is True
            assert result.data["dry_run"] is True
            assert len(log.records()) == 1
            assert (
                log.records()[0].approval_outcome
                is ApprovalOutcome.NOT_REQUIRED
            )
            invalid = await service.execute(
                IntegrationRequest(
                    connector="search",
                    operation="unsupported_operation",
                ),
                context,
            )
            assert invalid.success is False
            assert invalid.failure is not None
            batch = await service.execute_batch(
                [
                    IntegrationRequest(
                        connector="weather",
                        operation="current_weather",
                    ),
                    IntegrationRequest(
                        connector="market_data",
                        operation="latest_price",
                    ),
                ],
                context,
            )
            assert batch.execution_mode == "sequential_async_compatible"
            assert len(batch.results) == 2
            assert await session.scalar(
                select(func.count()).select_from(AuditLog)
            ) == 4

    asyncio.run(scenario())


def test_approval_rejection_is_logged_and_audited() -> None:
    async def scenario() -> None:
        async with sqlite_session() as session:
            context = await make_context(session)
            log = InMemoryIntegrationExecutionLog()
            service = IntegrationExecutionService(
                session,
                ConnectorFactory().build_registry(),
                log_sink=log,
            )
            with pytest.raises(IntegrationApprovalRequiredError):
                await service.execute(
                    IntegrationRequest(
                        connector="slack",
                        operation="send_message",
                    ),
                    context,
                )
            assert log.records()[0].result == "approval_required"
            assert await session.scalar(
                select(func.count()).select_from(AuditLog)
            ) == 1

    asyncio.run(scenario())


def test_default_logging_reuses_agent_execution_ledger() -> None:
    async def scenario() -> None:
        async with sqlite_session() as session:
            context = await make_context(session)
            service = IntegrationExecutionService(
                session, ConnectorFactory().build_registry()
            )
            await service.execute(
                IntegrationRequest(
                    connector="github",
                    operation="create_issue",
                    approval=ApprovalGrant(
                        policy=ApprovalPolicy.USER,
                        outcome=ApprovalOutcome.APPROVED,
                        approved_by=context.user.id,
                        approved_at=datetime.now(timezone.utc),
                    ),
                ),
                context,
            )
            assert await session.scalar(
                select(func.count()).select_from(AgentToolDefinition)
            ) == 1
            assert await session.scalar(
                select(func.count()).select_from(AgentToolExecution)
            ) == 1

    asyncio.run(scenario())
