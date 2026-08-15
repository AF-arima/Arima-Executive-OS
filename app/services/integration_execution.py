from __future__ import annotations

from datetime import datetime, timezone
from time import perf_counter

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import AuditAction, AuditEntity
from app.core.redaction import safe_failure_detail
from app.integrations.context import IntegrationExecutionContext
from app.integrations.exceptions import (
    IntegrationApprovalRequiredError,
    IntegrationPermissionDeniedError,
)
from app.integrations.logging import (
    DatabaseIntegrationExecutionLog,
    IntegrationExecutionLogSink,
)
from app.integrations.permissions import IntegrationPermissionValidator
from app.integrations.registry import ConnectorRegistry
from app.integrations.schemas import (
    ApprovalOutcome,
    ConnectorResult,
    IntegrationBatchResult,
    IntegrationExecutionRecord,
    IntegrationProvider,
    IntegrationRequest,
)
from app.services.audit import record_audit


class IntegrationExecutionService:
    def __init__(
        self,
        session: AsyncSession,
        registry: ConnectorRegistry,
        *,
        permission_validator: IntegrationPermissionValidator | None = None,
        log_sink: IntegrationExecutionLogSink | None = None,
    ) -> None:
        self.session = session
        self.registry = registry
        self.permission_validator = (
            permission_validator or IntegrationPermissionValidator()
        )
        self.log_sink = log_sink or DatabaseIntegrationExecutionLog(
            session, registry
        )

    async def execute(
        self,
        request: IntegrationRequest,
        context: IntegrationExecutionContext,
    ) -> ConnectorResult:
        connector = self.registry.get(request.connector, request.version)
        started = perf_counter()
        timestamp = datetime.now(timezone.utc)
        try:
            validated = connector.validate_request(
                request.operation, request.payload
            )
        except Exception as error:
            duration = self._duration(started)
            result = ConnectorResult(
                success=False,
                failure=safe_failure_detail(
                    "Integration validation failed", error
                ),
                execution_duration_ms=duration,
                provider=connector.provider(),
                connector_version=connector.connector_version(),
                correlation_id=context.correlation_id,
                metadata={
                    "connector": connector.connector_name(),
                    "operation": request.operation,
                    "error_type": type(error).__name__,
                },
            )
            await self._record(
                request=request,
                context=context,
                provider=connector.provider(),
                duration=duration,
                result="validation_failure",
                approval_outcome=ApprovalOutcome.NOT_REQUIRED,
                timestamp=timestamp,
                action=AuditAction.STATUS_CHANGE,
            )
            return result
        try:
            decision = self.permission_validator.require(
                context, validated.operation, request.approval
            )
        except IntegrationPermissionDeniedError:
            await self._record(
                request=request,
                context=context,
                provider=connector.provider(),
                duration=self._duration(started),
                result="denied",
                approval_outcome=ApprovalOutcome.DENIED,
                timestamp=timestamp,
                action=AuditAction.STATUS_CHANGE,
            )
            raise
        except IntegrationApprovalRequiredError:
            approval_outcome = (
                request.approval.outcome
                if request.approval is not None
                else ApprovalOutcome.PENDING
            )
            await self._record(
                request=request,
                context=context,
                provider=connector.provider(),
                duration=self._duration(started),
                result="approval_required",
                approval_outcome=approval_outcome,
                timestamp=timestamp,
                action=AuditAction.STATUS_CHANGE,
            )
            raise

        try:
            data = (
                await connector.dry_run(validated, context)
                if request.dry_run
                else await connector.execute(validated, context)
            )
            duration = self._duration(started)
            result = ConnectorResult(
                success=True,
                data=data,
                execution_duration_ms=duration,
                provider=connector.provider(),
                connector_version=connector.connector_version(),
                correlation_id=context.correlation_id,
                metadata={
                    "connector": connector.connector_name(),
                    "operation": request.operation,
                    "dry_run": request.dry_run,
                },
            )
            outcome = "success"
        except Exception as error:
            duration = self._duration(started)
            result = ConnectorResult(
                success=False,
                failure=safe_failure_detail(
                    "Integration execution failed", error
                ),
                execution_duration_ms=duration,
                provider=connector.provider(),
                connector_version=connector.connector_version(),
                correlation_id=context.correlation_id,
                metadata={
                    "connector": connector.connector_name(),
                    "operation": request.operation,
                    "error_type": type(error).__name__,
                },
            )
            outcome = "failure"
        await self._record(
            request=request,
            context=context,
            provider=connector.provider(),
            duration=duration,
            result=outcome,
            approval_outcome=decision.approval_outcome,
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
        requests: list[IntegrationRequest],
        context: IntegrationExecutionContext,
    ) -> IntegrationBatchResult:
        results = [
            await self.execute(request, context) for request in requests
        ]
        return IntegrationBatchResult(
            results=results,
            execution_mode="sequential_async_compatible",
            correlation_id=context.correlation_id,
        )

    async def _record(
        self,
        *,
        request: IntegrationRequest,
        context: IntegrationExecutionContext,
        provider: IntegrationProvider,
        duration: float,
        result: str,
        approval_outcome: ApprovalOutcome,
        timestamp: datetime,
        action: AuditAction,
    ) -> None:
        record_audit(
            self.session,
            actor_id=context.user.id,
            action=action,
            entity=AuditEntity.AUTOMATION,
            entity_id=context.correlation_id,
        )
        await self.log_sink.record(
            IntegrationExecutionRecord(
                connector=request.connector,
                provider=provider,
                user_id=context.user.id,
                agent_id=context.agent.id,
                run_id=context.run.id,
                operation=request.operation,
                duration_ms=duration,
                result=result,
                approval_outcome=approval_outcome,
                timestamp=timestamp,
                correlation_id=context.correlation_id,
            )
        )
        await self.session.commit()

    @staticmethod
    def _duration(started: float) -> float:
        return max(round((perf_counter() - started) * 1000, 3), 0.0)
