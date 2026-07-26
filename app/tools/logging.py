from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from typing import Protocol, cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    AgentRiskLevel,
    AgentToolDefinition,
    AgentToolExecution,
    ToolExecutionMode,
    ToolExecutionStatus,
)
from app.tools.registry import ToolRegistry
from app.tools.schemas import ToolExecutionRecord


class ToolExecutionLogSink(Protocol):
    async def record(self, execution: ToolExecutionRecord) -> None: ...


class InMemoryToolExecutionLog:
    """Default process-local sink, replaceable through dependency injection."""

    def __init__(self) -> None:
        self._records: list[ToolExecutionRecord] = []

    async def record(self, execution: ToolExecutionRecord) -> None:
        self._records.append(execution)

    def records(self) -> Sequence[ToolExecutionRecord]:
        return tuple(self._records)


class DatabaseToolExecutionLog:
    """Persists tool telemetry through the existing Agent Platform tables."""

    def __init__(
        self,
        session: AsyncSession,
        registry: ToolRegistry,
    ) -> None:
        self.session = session
        self.registry = registry

    async def record(self, execution: ToolExecutionRecord) -> None:
        tool = self.registry.get(execution.tool)
        definition = await self.session.scalar(
            select(AgentToolDefinition).where(
                AgentToolDefinition.slug == tool.tool_name()
            )
        )
        if definition is None:
            definition = AgentToolDefinition(
                slug=tool.tool_name(),
                name=tool.tool_name(),
                description=tool.tool_description(),
                category=tool.tool_category().value,
                risk_level=(
                    AgentRiskLevel.MEDIUM
                    if "write"
                    in {
                        permission.value
                        for permission in tool.required_permissions()
                    }
                    else AgentRiskLevel.LOW
                ),
                execution_mode=ToolExecutionMode.INTERNAL,
                requires_approval=False,
                is_enabled=True,
                input_schema=cast(
                    dict[str, object], tool.input_schema()
                ),
                output_schema=cast(
                    dict[str, object], tool.output_schema()
                ),
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
                input_payload={},
                output_payload={
                    "result": execution.result,
                    "permission_outcome": execution.permission_outcome.value,
                    "correlation_id": str(execution.correlation_id),
                    "agent_id": str(execution.agent_id),
                    "user_id": str(execution.user_id),
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
