from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    AgentRiskLevel,
    AgentToolDefinition,
    AgentToolExecution,
    ToolExecutionMode,
    ToolExecutionStatus,
)
from app.integrations.registry import ConnectorRegistry
from app.integrations.schemas import IntegrationExecutionRecord


class IntegrationExecutionLogSink(Protocol):
    async def record(self, execution: IntegrationExecutionRecord) -> None: ...


class InMemoryIntegrationExecutionLog:
    def __init__(self) -> None:
        self._records: list[IntegrationExecutionRecord] = []

    async def record(self, execution: IntegrationExecutionRecord) -> None:
        self._records.append(execution)

    def records(self) -> Sequence[IntegrationExecutionRecord]:
        return tuple(self._records)


class DatabaseIntegrationExecutionLog:
    """Uses the existing Agent Platform execution ledger."""

    def __init__(
        self,
        session: AsyncSession,
        registry: ConnectorRegistry,
    ) -> None:
        self.session = session
        self.registry = registry

    async def record(self, execution: IntegrationExecutionRecord) -> None:
        connector = self.registry.get(execution.connector)
        slug = f"integration.{connector.connector_name()}"
        definition = await self.session.scalar(
            select(AgentToolDefinition).where(
                AgentToolDefinition.slug == slug
            )
        )
        if definition is None:
            metadata = connector.metadata()
            definition = AgentToolDefinition(
                slug=slug,
                name=metadata.name,
                description=metadata.description,
                category=f"integration:{metadata.provider.value}",
                risk_level=AgentRiskLevel.MEDIUM,
                execution_mode=ToolExecutionMode.PROVIDER,
                requires_approval=any(
                    operation.approval_policy.value != "none"
                    for operation in metadata.operations
                ),
                is_enabled=True,
                input_schema={"type": "object"},
                output_schema={"type": "object"},
            )
            self.session.add(definition)
            await self.session.flush()
        succeeded = execution.result == "success"
        self.session.add(
            AgentToolExecution(
                run_id=execution.run_id,
                tool_id=definition.id,
                status=(
                    ToolExecutionStatus.SUCCEEDED
                    if succeeded
                    else ToolExecutionStatus.FAILED
                ),
                input_payload={
                    "operation": execution.operation,
                    "provider": execution.provider.value,
                },
                output_payload={
                    "result": execution.result,
                    "approval_outcome": execution.approval_outcome.value,
                    "correlation_id": str(execution.correlation_id),
                    "user_id": str(execution.user_id),
                    "agent_id": str(execution.agent_id),
                },
                error_code=(
                    None if succeeded else execution.result.upper()
                ),
                error_message=None,
                started_at=execution.timestamp
                - timedelta(milliseconds=execution.duration_ms),
                completed_at=execution.timestamp,
                duration_ms=round(execution.duration_ms),
            )
        )
