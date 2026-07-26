from __future__ import annotations

from datetime import datetime
from time import perf_counter

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import AuditAction, AuditEntity
from app.services.audit import record_audit
from app.tools.context import ToolExecutionContext
from app.tools.exceptions import ToolPermissionDeniedError
from app.tools.logging import (
    DatabaseToolExecutionLog,
    ToolExecutionLogSink,
)
from app.tools.permissions import ToolPermissionValidator
from app.tools.registry import ToolRegistry
from app.tools.schemas import (
    PermissionOutcome,
    ToolBatchResult,
    ToolExecutionRecord,
    ToolExecutionRequest,
    ToolResult,
)


class ToolExecutionService:
    def __init__(
        self,
        session: AsyncSession,
        registry: ToolRegistry,
        *,
        permission_validator: ToolPermissionValidator | None = None,
        log_sink: ToolExecutionLogSink | None = None,
    ) -> None:
        self.session = session
        self.registry = registry
        self.permission_validator = (
            permission_validator or ToolPermissionValidator()
        )
        self.log_sink = log_sink or DatabaseToolExecutionLog(
            session, registry
        )

    async def execute(
        self,
        request: ToolExecutionRequest,
        context: ToolExecutionContext,
    ) -> ToolResult:
        tool = self.registry.get(request.tool_name)
        started = perf_counter()
        timestamp = context.current_timestamp
        try:
            self.permission_validator.require(
                context, tool.required_permissions()
            )
        except ToolPermissionDeniedError:
            duration = self._duration(started)
            await self._record(
                tool_name=tool.tool_name(),
                context=context,
                duration=duration,
                result="denied",
                permission_outcome=PermissionOutcome.DENIED,
                timestamp=timestamp,
                action=AuditAction.STATUS_CHANGE,
            )
            raise

        try:
            validated = tool.validate(request.payload)
            data = await tool.execute(validated, context)
            duration = self._duration(started)
            result = ToolResult(
                success=True,
                data=data,
                execution_time_ms=duration,
                tool_version=tool.tool_version(),
                correlation_id=context.correlation_id,
                metadata={
                    "tool": tool.tool_name(),
                    "category": tool.tool_category().value,
                },
            )
            outcome = "success"
        except Exception as error:
            duration = self._duration(started)
            result = ToolResult(
                success=False,
                failure=str(error),
                execution_time_ms=duration,
                tool_version=tool.tool_version(),
                correlation_id=context.correlation_id,
                metadata={
                    "tool": tool.tool_name(),
                    "category": tool.tool_category().value,
                    "error_type": type(error).__name__,
                },
            )
            outcome = "failure"

        await self._record(
            tool_name=tool.tool_name(),
            context=context,
            duration=duration,
            result=outcome,
            permission_outcome=PermissionOutcome.ALLOWED,
            timestamp=timestamp,
            action=(
                AuditAction.COMPLETE
                if result.success
                else AuditAction.STATUS_CHANGE
            ),
        )
        return result

    async def execute_batch(
        self,
        requests: list[ToolExecutionRequest],
        context: ToolExecutionContext,
    ) -> ToolBatchResult:
        results = [
            await self.execute(request, context) for request in requests
        ]
        return ToolBatchResult(
            results=results,
            execution_mode="sequential",
            correlation_id=context.correlation_id,
        )

    async def execute_parallel(
        self,
        requests: list[ToolExecutionRequest],
        context: ToolExecutionContext,
    ) -> ToolBatchResult:
        """Future-parallel abstraction; serial while sharing one DB session."""
        result = await self.execute_batch(requests, context)
        return result.model_copy(
            update={"execution_mode": "parallel_abstraction"}
        )

    async def _record(
        self,
        *,
        tool_name: str,
        context: ToolExecutionContext,
        duration: float,
        result: str,
        permission_outcome: PermissionOutcome,
        timestamp: datetime,
        action: AuditAction,
    ) -> None:
        record_audit(
            self.session,
            actor_id=context.current_user.id,
            action=action,
            entity=AuditEntity.AUTOMATION,
            entity_id=context.correlation_id,
        )
        await self.log_sink.record(
            ToolExecutionRecord(
                tool=tool_name,
                agent_id=context.current_agent.id,
                user_id=context.current_user.id,
                run_id=context.run.id,
                duration_ms=duration,
                result=result,
                permission_outcome=permission_outcome,
                timestamp=timestamp,
                correlation_id=context.correlation_id,
            )
        )
        await self.session.commit()

    @staticmethod
    def _duration(started: float) -> float:
        return max(round((perf_counter() - started) * 1000, 3), 0.0)
