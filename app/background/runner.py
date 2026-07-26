from __future__ import annotations

from time import perf_counter
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.background.clock import Clock, SystemClock
from app.background.context import BackgroundExecutionContext
from app.background.exceptions import (
    BackgroundApprovalRequiredError,
    BackgroundCancellationError,
    BackgroundConfigurationError,
    BackgroundPermissionDeniedError,
    BackgroundRetryExhaustedError,
    BackgroundTimeoutError,
)
from app.background.health import BackgroundHealthMonitor
from app.background.logging import (
    BackgroundLifecycleLogSink,
    DatabaseBackgroundLifecycleLog,
)
from app.background.permissions import BackgroundPermissionValidator
from app.background.policies import RetryPolicy, TimeoutPolicy
from app.background.registry import BackgroundJobRegistry
from app.background.schemas import (
    ApprovalOutcome,
    BackgroundBatchResult,
    BackgroundExecutionRequest,
    BackgroundExecutionResult,
    BackgroundJobState,
    BackgroundLifecycleRecord,
    BackgroundPermission,
    JobExecutionPlan,
)
from app.database.models import (
    BackgroundJobAttempt,
    BackgroundJobDefinition,
    BackgroundJobExecution,
)
from app.integrations.context import IntegrationExecutionContext
from app.integrations.schemas import (
    IntegrationEnvironment,
    IntegrationPermission,
    IntegrationRequest,
)
from app.services.agent_execution import ExecutionOrchestrator
from app.services.integration_execution import IntegrationExecutionService
from app.services.tool_execution import ToolExecutionService
from app.tools.context import ToolExecutionContext
from app.tools.schemas import ToolExecutionRequest, ToolPermission


class BackgroundJobRunner:
    def __init__(
        self,
        session: AsyncSession,
        registry: BackgroundJobRegistry,
        *,
        clock: Clock | None = None,
        permission_validator: BackgroundPermissionValidator | None = None,
        retry_policy: RetryPolicy | None = None,
        timeout_policy: TimeoutPolicy | None = None,
        tool_service: ToolExecutionService | None = None,
        integration_service: IntegrationExecutionService | None = None,
        agent_service: ExecutionOrchestrator | None = None,
        log_sink: BackgroundLifecycleLogSink | None = None,
    ) -> None:
        self.session = session
        self.registry = registry
        self.clock = clock or SystemClock()
        self.permission_validator = (
            permission_validator or BackgroundPermissionValidator()
        )
        self.retry_policy = retry_policy or RetryPolicy()
        self.timeout_policy = timeout_policy or TimeoutPolicy()
        self.tool_service = tool_service
        self.integration_service = integration_service
        self.agent_service = agent_service
        self.log_sink = log_sink or DatabaseBackgroundLifecycleLog(session)
        self.monitor = BackgroundHealthMonitor(self.clock)
        self._cancelled: set[UUID] = set()

    def cancel(self, correlation_id: UUID) -> None:
        self._cancelled.add(correlation_id)

    async def run(
        self,
        request: BackgroundExecutionRequest,
        context: BackgroundExecutionContext,
    ) -> BackgroundExecutionResult:
        job = self.registry.get(request.job_name)
        if context.job.job_name() != job.job_name():
            raise BackgroundConfigurationError(
                "Execution context job does not match request"
            )
        definition = await self._definition(job)
        execution = BackgroundJobExecution(
            job_definition_id=definition.id,
            schedule_id=request.schedule_id,
            job_name=job.job_name(),
            user_id=context.user.id,
            agent_id=context.agent.id,
            run_id=context.run.id,
            correlation_id=context.correlation_id,
            trigger_source=context.trigger_source,
            status=BackgroundJobState.PENDING,
            input_payload=request.payload,
            dry_run=request.dry_run,
            attempt_count=0,
        )
        self.session.add(execution)
        await self.session.flush()
        await self._transition(
            context,
            BackgroundJobState.PENDING,
            None,
            execution_id=execution.id,
            attempt=1,
            result="created",
        )
        try:
            decision = self.permission_validator.require(
                context, request.approval
            )
        except BackgroundApprovalRequiredError:
            execution.status = BackgroundJobState.WAITING_FOR_APPROVAL
            await self._transition(
                context,
                BackgroundJobState.WAITING_FOR_APPROVAL,
                BackgroundJobState.PENDING,
                execution_id=execution.id,
                attempt=1,
                result="approval_required",
                approval=ApprovalOutcome.PENDING,
                permission="allowed",
            )
            await self.session.commit()
            raise
        except BackgroundPermissionDeniedError:
            execution.status = BackgroundJobState.BLOCKED
            await self._transition(
                context,
                BackgroundJobState.BLOCKED,
                BackgroundJobState.PENDING,
                execution_id=execution.id,
                attempt=1,
                result="permission_denied",
                approval=ApprovalOutcome.DENIED,
                permission="denied",
            )
            await self.session.commit()
            raise

        started_at = self.clock.now()
        started_counter = perf_counter()
        execution.status = BackgroundJobState.RUNNING
        execution.started_at = started_at
        self.monitor.active_jobs += 1
        await self._transition(
            context,
            BackgroundJobState.RUNNING,
            BackgroundJobState.PENDING,
            execution_id=execution.id,
            attempt=1,
            result="started",
            approval=decision.approval_outcome,
        )
        last_error: Exception | None = None
        for attempt_number in range(1, self.retry_policy.maximum_attempts + 1):
            execution.attempt_count = attempt_number
            attempt = BackgroundJobAttempt(
                execution_id=execution.id,
                attempt_number=attempt_number,
                status=BackgroundJobState.RUNNING,
                started_at=self.clock.now(),
            )
            self.session.add(attempt)
            try:
                self._ensure_not_cancelled(context.correlation_id)
                validated = job.validate(request.payload)
                plan = await job.execute(validated, context)
                output = await self._execute_plan(
                    plan, context, request.dry_run
                )
                duration = self._duration(started_counter)
                if duration > self.timeout_policy.execution_timeout_seconds * 1000:
                    raise BackgroundTimeoutError(
                        "Background execution timeout exceeded"
                    )
                completed_at = self.clock.now()
                attempt.status = BackgroundJobState.SUCCEEDED
                attempt.completed_at = completed_at
                attempt.duration_ms = duration
                attempt.result_payload = output
                execution.status = BackgroundJobState.SUCCEEDED
                execution.completed_at = completed_at
                execution.duration_ms = duration
                execution.result_payload = output
                self.monitor.active_jobs -= 1
                self.monitor.last_success = completed_at
                await self._transition(
                    context,
                    BackgroundJobState.SUCCEEDED,
                    BackgroundJobState.RUNNING,
                    execution_id=execution.id,
                    attempt=attempt_number,
                    duration=duration,
                    result="success",
                    approval=decision.approval_outcome,
                )
                await self.session.commit()
                return BackgroundExecutionResult(
                    success=True,
                    status=BackgroundJobState.SUCCEEDED,
                    metadata=output,
                    duration_ms=duration,
                    attempt_number=attempt_number,
                    job_version=job.job_version(),
                    correlation_id=context.correlation_id,
                    started_at=started_at,
                    completed_at=completed_at,
                    next_run_at=(
                        context.schedule.next_run_at
                        if context.schedule is not None
                        else None
                    ),
                )
            except Exception as error:
                last_error = error
                attempt.status = (
                    BackgroundJobState.CANCELLED
                    if isinstance(error, BackgroundCancellationError)
                    else BackgroundJobState.FAILED
                )
                attempt.completed_at = self.clock.now()
                attempt.error_code = type(error).__name__
                attempt.error_message = str(error)
                if not self.retry_policy.should_retry(error, attempt_number):
                    break
        duration = self._duration(started_counter)
        completed_at = self.clock.now()
        cancelled = isinstance(last_error, BackgroundCancellationError)
        final_state = (
            BackgroundJobState.CANCELLED
            if cancelled
            else BackgroundJobState.FAILED
        )
        execution.status = final_state
        execution.completed_at = completed_at
        execution.duration_ms = duration
        execution.error_code = (
            type(last_error).__name__ if last_error is not None else None
        )
        execution.error_message = str(last_error) if last_error else None
        self.monitor.active_jobs -= 1
        self.monitor.last_failure = completed_at
        self.monitor.failed_jobs += 0 if cancelled else 1
        await self._transition(
            context,
            final_state,
            BackgroundJobState.RUNNING,
            execution_id=execution.id,
            attempt=execution.attempt_count,
            duration=duration,
            result="cancelled" if cancelled else "failed",
            approval=decision.approval_outcome,
        )
        await self.session.commit()
        if (
            last_error is not None
            and self.retry_policy.maximum_attempts > 1
            and not cancelled
        ):
            raise BackgroundRetryExhaustedError(str(last_error)) from last_error
        if last_error is not None:
            raise last_error
        raise BackgroundRetryExhaustedError("Background execution failed")

    async def run_batch(
        self,
        items: list[
            tuple[BackgroundExecutionRequest, BackgroundExecutionContext]
        ],
    ) -> BackgroundBatchResult:
        results = [
            await self.run(request, context) for request, context in items
        ]
        correlation_id = (
            items[0][1].correlation_id if items else UUID(int=0)
        )
        return BackgroundBatchResult(
            results=results,
            execution_mode="sequential",
            correlation_id=correlation_id,
        )

    async def run_parallel(
        self,
        items: list[
            tuple[BackgroundExecutionRequest, BackgroundExecutionContext]
        ],
    ) -> BackgroundBatchResult:
        result = await self.run_batch(items)
        return result.model_copy(
            update={"execution_mode": "parallel_abstraction"}
        )

    async def _execute_plan(
        self,
        plan: JobExecutionPlan,
        context: BackgroundExecutionContext,
        dry_run: bool,
    ) -> dict[str, object]:
        if dry_run or plan.target == "mock":
            return {
                "target": plan.target,
                "dry_run": dry_run,
                **plan.mock_result,
            }
        if plan.target == "internal_tool":
            if self.tool_service is None or plan.target_name is None:
                raise BackgroundConfigurationError(
                    "Internal tool execution service is unavailable"
                )
            tool_result = await self.tool_service.execute(
                ToolExecutionRequest(
                    tool_name=plan.target_name, payload=plan.payload
                ),
                self._tool_context(context),
            )
            return tool_result.model_dump(mode="json")
        if plan.target == "integration":
            if (
                self.integration_service is None
                or plan.target_name is None
                or plan.operation is None
            ):
                raise BackgroundConfigurationError(
                    "Integration execution service is unavailable"
                )
            integration_result = await self.integration_service.execute(
                IntegrationRequest(
                    connector=plan.target_name,
                    operation=plan.operation,
                    payload=plan.payload,
                    dry_run=False,
                ),
                self._integration_context(context),
            )
            return integration_result.model_dump(mode="json")
        if self.agent_service is None:
            raise BackgroundConfigurationError(
                "Agent execution service is unavailable"
            )
        agent_result = await self.agent_service.execute_queued(
            context.run.id, context.user, provider_name="mock"
        )
        return {
            "run_id": str(agent_result.run_id),
            "provider": agent_result.provider_name,
            "delegated": True,
        }

    async def _definition(self, job: object) -> BackgroundJobDefinition:
        from app.background.base import BackgroundJob

        if not isinstance(job, BackgroundJob):
            raise BackgroundConfigurationError("Invalid background job")
        definition = await self.session.scalar(
            select(BackgroundJobDefinition).where(
                BackgroundJobDefinition.job_name == job.job_name(),
                BackgroundJobDefinition.version == job.job_version(),
            )
        )
        if definition is None:
            metadata = job.metadata()
            definition = BackgroundJobDefinition(
                job_name=metadata.name,
                version=metadata.version,
                description=metadata.description,
                category=metadata.category.value,
                job_type=metadata.job_type.value,
                required_permissions=[
                    item.value for item in metadata.permissions
                ],
                approval_policy=metadata.approval_policy.value,
                capabilities=[
                    item.value for item in metadata.capabilities
                ],
                input_schema=metadata.input_schema,
                output_schema=metadata.output_schema,
                enabled=True,
            )
            self.session.add(definition)
            await self.session.flush()
        return definition

    async def _transition(
        self,
        context: BackgroundExecutionContext,
        to_state: BackgroundJobState,
        from_state: BackgroundJobState | None,
        *,
        execution_id: UUID,
        attempt: int,
        result: str,
        duration: float = 0,
        approval: ApprovalOutcome = ApprovalOutcome.NOT_REQUIRED,
        permission: str = "allowed",
    ) -> None:
        await self.log_sink.record(
            BackgroundLifecycleRecord(
                job=context.job.job_name(),
                schedule_id=(
                    context.schedule.id
                    if context.schedule is not None
                    else None
                ),
                execution_id=execution_id,
                user_id=context.user.id,
                agent_id=context.agent.id,
                trigger=context.trigger_source,
                from_state=from_state,
                to_state=to_state,
                attempt=attempt,
                duration_ms=duration,
                result=result,
                approval_outcome=approval,
                permission_outcome=permission,
                timestamp=self.clock.now(),
                correlation_id=context.correlation_id,
            )
        )

    def _ensure_not_cancelled(self, correlation_id: UUID) -> None:
        if correlation_id in self._cancelled:
            raise BackgroundCancellationError(
                "Background execution was cancelled"
            )

    @staticmethod
    def _duration(started: float) -> float:
        return max(round((perf_counter() - started) * 1000, 3), 0.0)

    @staticmethod
    def _tool_context(
        context: BackgroundExecutionContext,
    ) -> ToolExecutionContext:
        mapping = {
            BackgroundPermission.READ: ToolPermission.READ,
            BackgroundPermission.WRITE: ToolPermission.WRITE,
            BackgroundPermission.ADMIN: ToolPermission.ADMIN,
            BackgroundPermission.APPROVAL_REQUIRED: ToolPermission.AUDIT,
        }
        permissions = frozenset(
            target
            for source, target in mapping.items()
            if source in context.permissions
        )
        return ToolExecutionContext(
            current_user=context.user,
            current_agent=context.agent,
            conversation=context.conversation,
            run=context.run,
            permissions=permissions,
            correlation_id=context.correlation_id,
            timezone=context.timezone,
            locale=context.locale,
            current_timestamp=context.current_timestamp,
        )

    @staticmethod
    def _integration_context(
        context: BackgroundExecutionContext,
    ) -> IntegrationExecutionContext:
        mapping = {
            BackgroundPermission.READ: IntegrationPermission.READ,
            BackgroundPermission.WRITE: IntegrationPermission.WRITE,
            BackgroundPermission.ADMIN: IntegrationPermission.ADMIN,
            BackgroundPermission.APPROVAL_REQUIRED: (
                IntegrationPermission.APPROVAL_REQUIRED
            ),
            BackgroundPermission.SENSITIVE_DATA: (
                IntegrationPermission.SENSITIVE_DATA
            ),
        }
        permissions = frozenset(
            target
            for source, target in mapping.items()
            if source in context.permissions
        )
        return IntegrationExecutionContext(
            user=context.user,
            agent=context.agent,
            conversation=context.conversation,
            run=context.run,
            user_permissions=permissions,
            agent_permissions=permissions,
            integration_permissions=permissions,
            correlation_id=context.correlation_id,
            timezone=context.timezone,
            locale=context.locale,
            environment=IntegrationEnvironment(context.environment.value),
        )

    async def health(self):
        return self.monitor.snapshot()
