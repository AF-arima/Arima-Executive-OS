from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.background.base import BackgroundJob
from app.background.clock import Clock, SystemClock
from app.background.context import BackgroundExecutionContext
from app.background.dispatcher import (
    BackgroundDispatcher,
    BackgroundDispatchItem,
)
from app.background.exceptions import (
    BackgroundJobNotFoundError,
    BackgroundValidationError,
)
from app.background.factory import BackgroundJobFactory
from app.background.registry import BackgroundJobRegistry
from app.background.runner import BackgroundJobRunner
from app.background.scheduler import BackgroundScheduler
from app.background.schemas import (
    BackgroundExecutionRequest,
    BackgroundExecutionResult,
    BackgroundJobState,
    BackgroundTriggerSource,
    CalendarSchedule,
    ConditionalSchedule,
    IntervalSchedule,
    OneTimeSchedule,
    RecurringSchedule,
    ScheduleDefinition,
    ScheduleType,
)
from app.database.models import (
    AuditAction,
    AuditEntity,
    BackgroundJobDefinition,
    BackgroundJobEvent,
    BackgroundJobExecution,
    BackgroundJobSchedule,
)
from app.integrations.factory import ConnectorFactory
from app.services.agent_execution import ExecutionOrchestrator
from app.services.audit import record_audit
from app.services.integration_execution import IntegrationExecutionService
from app.services.tool_execution import ToolExecutionService
from app.tools.factory import ToolFactory

ContextFactory = Callable[
    [BackgroundJobSchedule, BackgroundJob], BackgroundExecutionContext
]


class BackgroundExecutionService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        clock: Clock | None = None,
        registry: BackgroundJobRegistry | None = None,
        runner: BackgroundJobRunner | None = None,
    ) -> None:
        self.session = session
        self.clock = clock or SystemClock()
        self.registry = registry or BackgroundJobFactory(
            clock=self.clock
        ).build_registry()
        self.scheduler = BackgroundScheduler(self.clock)
        if runner is None:
            tool_service = ToolExecutionService(
                session, ToolFactory(session).create_registry()
            )
            integration_service = IntegrationExecutionService(
                session, ConnectorFactory().build_registry()
            )
            runner = BackgroundJobRunner(
                session,
                self.registry,
                clock=self.clock,
                tool_service=tool_service,
                integration_service=integration_service,
                agent_service=ExecutionOrchestrator.deterministic(session),
            )
        self.runner = runner
        self.dispatcher = BackgroundDispatcher(
            self.scheduler, self.runner
        )

    async def register_job(
        self, job: BackgroundJob
    ) -> BackgroundJobDefinition:
        try:
            existing_job = self.registry.get(
                job.job_name(), job.job_version()
            )
        except BackgroundJobNotFoundError:
            self.registry.register(job)
        else:
            if existing_job is not job:
                job = existing_job
        existing = await self.session.scalar(
            select(BackgroundJobDefinition).where(
                BackgroundJobDefinition.job_name == job.job_name(),
                BackgroundJobDefinition.version == job.job_version(),
            )
        )
        if existing is not None:
            return existing
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
            capabilities=[item.value for item in metadata.capabilities],
            input_schema=metadata.input_schema,
            output_schema=metadata.output_schema,
            enabled=True,
        )
        self.session.add(definition)
        await self.session.commit()
        return definition

    async def create_schedule(
        self,
        schedule: ScheduleDefinition,
        context: BackgroundExecutionContext,
    ) -> BackgroundJobSchedule:
        job = self.registry.get(schedule.job_name)
        definition = await self.register_job(job)
        next_run = schedule.next_run_at or self.scheduler.next_run(
            schedule, after=self.clock.now()
        )
        persisted = BackgroundJobSchedule(
            job_definition_id=definition.id,
            job_name=schedule.job_name,
            user_id=context.user.id,
            agent_id=context.agent.id,
            conversation_id=context.conversation.id,
            run_id=context.run.id,
            schedule_type=schedule.schedule_type,
            status=(
                BackgroundJobState.PAUSED
                if schedule.paused
                else BackgroundJobState.SCHEDULED
            ),
            definition=cast(
                dict[str, object], schedule.model_dump(mode="json")
            ),
            timezone=schedule.timezone,
            start_at=schedule.start_at,
            end_at=schedule.end_at,
            next_run_at=next_run,
            last_run_at=schedule.last_run_at,
            maximum_runs=schedule.maximum_runs,
            run_count=schedule.run_count,
            enabled=schedule.enabled,
            paused=schedule.paused,
        )
        self.session.add(persisted)
        await self.session.flush()
        await self._schedule_event(
            persisted,
            context,
            None,
            persisted.status,
            "schedule_created",
        )
        await self.session.commit()
        return persisted

    async def update_schedule(
        self,
        schedule_id: UUID,
        schedule: ScheduleDefinition,
        context: BackgroundExecutionContext,
    ) -> BackgroundJobSchedule:
        persisted = await self._schedule(schedule_id)
        if schedule.job_name != persisted.job_name:
            raise BackgroundValidationError("Schedule job cannot be changed")
        previous = persisted.status
        persisted.definition = cast(
            dict[str, object], schedule.model_dump(mode="json")
        )
        persisted.timezone = schedule.timezone
        persisted.start_at = schedule.start_at
        persisted.end_at = schedule.end_at
        persisted.next_run_at = schedule.next_run_at
        persisted.maximum_runs = schedule.maximum_runs
        persisted.enabled = schedule.enabled
        persisted.paused = schedule.paused
        persisted.status = (
            BackgroundJobState.PAUSED
            if schedule.paused
            else BackgroundJobState.SCHEDULED
        )
        await self._schedule_event(
            persisted,
            context,
            previous,
            persisted.status,
            "schedule_updated",
        )
        await self.session.commit()
        return persisted

    async def pause_schedule(
        self, schedule_id: UUID, context: BackgroundExecutionContext
    ) -> BackgroundJobSchedule:
        return await self._set_schedule_state(
            schedule_id,
            context,
            BackgroundJobState.PAUSED,
            paused=True,
            enabled=True,
            event="schedule_paused",
        )

    async def resume_schedule(
        self, schedule_id: UUID, context: BackgroundExecutionContext
    ) -> BackgroundJobSchedule:
        return await self._set_schedule_state(
            schedule_id,
            context,
            BackgroundJobState.SCHEDULED,
            paused=False,
            enabled=True,
            event="schedule_resumed",
        )

    async def cancel_schedule(
        self, schedule_id: UUID, context: BackgroundExecutionContext
    ) -> BackgroundJobSchedule:
        schedule = await self._set_schedule_state(
            schedule_id,
            context,
            BackgroundJobState.CANCELLED,
            paused=False,
            enabled=False,
            event="schedule_cancelled",
        )
        schedule.cancelled_at = self.clock.now()
        await self.session.commit()
        return schedule

    async def list_due_jobs(self) -> list[BackgroundJobSchedule]:
        rows = await self.session.scalars(
            select(BackgroundJobSchedule)
            .where(
                BackgroundJobSchedule.enabled.is_(True),
                BackgroundJobSchedule.paused.is_(False),
                BackgroundJobSchedule.status
                == BackgroundJobState.SCHEDULED,
                BackgroundJobSchedule.next_run_at <= self.clock.now(),
            )
            .order_by(BackgroundJobSchedule.next_run_at)
        )
        return list(rows.all())

    async def list_active_for_user(
        self,
        user_id: UUID,
        *,
        limit: int,
    ) -> list[BackgroundJobSchedule]:
        """Return a bounded list of a user's active persisted schedules."""
        rows = await self.session.scalars(
            select(BackgroundJobSchedule)
            .where(
                BackgroundJobSchedule.user_id == user_id,
                BackgroundJobSchedule.enabled.is_(True),
                BackgroundJobSchedule.paused.is_(False),
                BackgroundJobSchedule.cancelled_at.is_(None),
            )
            .order_by(
                BackgroundJobSchedule.next_run_at.asc().nulls_last(),
                BackgroundJobSchedule.id.asc(),
            )
            .limit(limit)
        )
        return list(rows.all())

    async def execute_due_jobs(
        self, context_factory: ContextFactory
    ) -> list[BackgroundExecutionResult]:
        results = []
        for persisted in await self.list_due_jobs():
            job = self.registry.get(persisted.job_name)
            context = context_factory(persisted, job)
            schedule = self._decode_schedule(persisted)
            context = replace(context, schedule=schedule)
            result = await self.dispatcher.dispatch(
                BackgroundDispatchItem(
                    BackgroundExecutionRequest(
                        job_name=persisted.job_name,
                        payload=schedule.input_payload,
                        schedule_id=persisted.id,
                    ),
                    context,
                )
            )
            persisted.run_count += 1
            persisted.last_run_at = self.clock.now()
            if (
                persisted.maximum_runs is not None
                and persisted.run_count >= persisted.maximum_runs
            ):
                persisted.next_run_at = None
            else:
                persisted.next_run_at = self.scheduler.next_run(
                    schedule, after=self.clock.now()
                )
            persisted.definition = {
                **persisted.definition,
                "run_count": persisted.run_count,
                "last_run_at": persisted.last_run_at.isoformat(),
                "next_run_at": (
                    persisted.next_run_at.isoformat()
                    if persisted.next_run_at is not None
                    else None
                ),
            }
            if persisted.next_run_at is None:
                persisted.enabled = False
                persisted.status = BackgroundJobState.EXPIRED
            await self.session.commit()
            results.append(result)
        return results

    async def execute_job_now(
        self,
        request: BackgroundExecutionRequest,
        context: BackgroundExecutionContext,
    ) -> BackgroundExecutionResult:
        return await self.runner.run(request, context)

    async def dry_run_execution(
        self,
        request: BackgroundExecutionRequest,
        context: BackgroundExecutionContext,
    ) -> BackgroundExecutionResult:
        return await self.runner.run(
            request.model_copy(update={"dry_run": True}), context
        )

    async def retry_execution(
        self,
        execution_id: UUID,
        context: BackgroundExecutionContext,
    ) -> BackgroundExecutionResult:
        execution = await self.session.get(
            BackgroundJobExecution, execution_id
        )
        if execution is None:
            raise BackgroundValidationError("Execution not found")
        if execution.status is not BackgroundJobState.FAILED:
            raise BackgroundValidationError(
                "Only failed executions can be retried"
            )
        retry_context = replace(
            context,
            correlation_id=uuid4(),
            trigger_source=BackgroundTriggerSource.RETRY,
            current_timestamp=self.clock.now(),
        )
        return await self.runner.run(
            BackgroundExecutionRequest(
                job_name=execution.job_name,
                payload=execution.input_payload,
                schedule_id=execution.schedule_id,
            ),
            retry_context,
        )

    async def get_execution_history(
        self,
        *,
        job_name: str | None = None,
        limit: int = 100,
    ) -> list[BackgroundJobExecution]:
        statement = select(BackgroundJobExecution)
        if job_name is not None:
            statement = statement.where(
                BackgroundJobExecution.job_name == job_name
            )
        rows = await self.session.scalars(
            statement.order_by(
                BackgroundJobExecution.created_at.desc()
            ).limit(limit)
        )
        return list(rows.all())

    async def _set_schedule_state(
        self,
        schedule_id: UUID,
        context: BackgroundExecutionContext,
        state: BackgroundJobState,
        *,
        paused: bool,
        enabled: bool,
        event: str,
    ) -> BackgroundJobSchedule:
        schedule = await self._schedule(schedule_id)
        previous = schedule.status
        schedule.status = state
        schedule.paused = paused
        schedule.enabled = enabled
        await self._schedule_event(
            schedule, context, previous, state, event
        )
        await self.session.commit()
        return schedule

    async def _schedule(self, schedule_id: UUID) -> BackgroundJobSchedule:
        schedule = await self.session.get(
            BackgroundJobSchedule, schedule_id
        )
        if schedule is None:
            raise BackgroundValidationError("Schedule not found")
        return schedule

    async def _schedule_event(
        self,
        schedule: BackgroundJobSchedule,
        context: BackgroundExecutionContext,
        from_state: BackgroundJobState | None,
        to_state: BackgroundJobState,
        event_type: str,
    ) -> None:
        self.session.add(
            BackgroundJobEvent(
                schedule_id=schedule.id,
                execution_id=None,
                job_name=schedule.job_name,
                correlation_id=context.correlation_id,
                from_state=from_state,
                to_state=to_state,
                event_type=event_type,
                event_metadata={},
                timestamp=self.clock.now(),
            )
        )
        record_audit(
            self.session,
            actor_id=context.user.id,
            action=AuditAction.STATUS_CHANGE,
            entity=AuditEntity.AUTOMATION,
            entity_id=context.correlation_id,
        )

    @staticmethod
    def _decode_schedule(
        schedule: BackgroundJobSchedule,
    ) -> ScheduleDefinition:
        if schedule.schedule_type is ScheduleType.ONE_TIME:
            return OneTimeSchedule.model_validate(schedule.definition)
        if schedule.schedule_type is ScheduleType.RECURRING:
            return RecurringSchedule.model_validate(schedule.definition)
        if schedule.schedule_type is ScheduleType.INTERVAL:
            return IntervalSchedule.model_validate(schedule.definition)
        if schedule.schedule_type is ScheduleType.CALENDAR:
            return CalendarSchedule.model_validate(schedule.definition)
        return ConditionalSchedule.model_validate(schedule.definition)
