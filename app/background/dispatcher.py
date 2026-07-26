from __future__ import annotations

from dataclasses import dataclass

from app.background.context import BackgroundExecutionContext
from app.background.exceptions import BackgroundValidationError
from app.background.health import BackgroundHealthMonitor
from app.background.permissions import BackgroundPermissionValidator
from app.background.runner import BackgroundJobRunner
from app.background.scheduler import BackgroundScheduler
from app.background.schemas import (
    BackgroundExecutionRequest,
    BackgroundExecutionResult,
)


@dataclass(frozen=True, slots=True)
class BackgroundDispatchItem:
    request: BackgroundExecutionRequest
    context: BackgroundExecutionContext


class BackgroundDispatcher:
    def __init__(
        self,
        scheduler: BackgroundScheduler,
        runner: BackgroundJobRunner,
        permission_validator: BackgroundPermissionValidator | None = None,
    ) -> None:
        self.scheduler = scheduler
        self.runner = runner
        self.permission_validator = (
            permission_validator or BackgroundPermissionValidator()
        )
        self.monitor = BackgroundHealthMonitor(scheduler.clock)

    async def dispatch(
        self, item: BackgroundDispatchItem
    ) -> BackgroundExecutionResult:
        context = item.context
        if context.schedule is not None:
            if context.schedule.job_name != item.request.job_name:
                raise BackgroundValidationError(
                    "Schedule job does not match execution request"
                )
            if not self.scheduler.is_due(context.schedule):
                raise BackgroundValidationError("Schedule is not due")
        self.permission_validator.require(
            context, item.request.approval
        )
        self.monitor.last_dispatch = self.scheduler.clock.now()
        try:
            result = await self.runner.run(item.request, context)
        except Exception:
            self.monitor.last_failure = self.scheduler.clock.now()
            raise
        self.monitor.last_success = self.scheduler.clock.now()
        return result

    async def dispatch_due(
        self, items: list[BackgroundDispatchItem]
    ) -> list[BackgroundExecutionResult]:
        return [await self.dispatch(item) for item in items]

    async def health(self):
        return self.monitor.snapshot()
