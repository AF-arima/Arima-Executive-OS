import asyncio
from dataclasses import replace

import pytest

from app.background.clock import FixedClock
from app.background.dispatcher import (
    BackgroundDispatcher,
    BackgroundDispatchItem,
)
from app.background.exceptions import (
    BackgroundApprovalRequiredError,
    BackgroundValidationError,
)
from app.background.factory import BackgroundJobFactory
from app.background.jobs.base import DeterministicBackgroundJob
from app.background.runner import BackgroundJobRunner
from app.background.scheduler import BackgroundScheduler
from app.background.schemas import (
    ApprovalPolicy,
    BackgroundExecutionRequest,
    BackgroundJobCategory,
    JobExecutionPlan,
    OneTimeSchedule,
)
from tests.background.helpers import FIXED_NOW, make_context
from tests.database.helpers import sqlite_session


class ApprovalJob(DeterministicBackgroundJob):
    name = "approval_job"
    description = "Approval test job."
    category = BackgroundJobCategory.EXECUTIVE
    approval_policy = ApprovalPolicy.USER
    plan = JobExecutionPlan(
        target="mock", mock_result={"approval": "granted"}
    )


def test_approval_blocks_runner_and_dispatcher_validates_schedule() -> None:
    async def scenario() -> None:
        async with sqlite_session() as session:
            factory = BackgroundJobFactory(
                clock=FixedClock(FIXED_NOW),
                builders=(ApprovalJob,),
            )
            registry = factory.build_registry()
            job = registry.get("approval_job")
            context = await make_context(session, job)
            runner = BackgroundJobRunner(
                session, registry, clock=FixedClock(FIXED_NOW)
            )
            with pytest.raises(BackgroundApprovalRequiredError):
                await runner.run(
                    BackgroundExecutionRequest(job_name=job.job_name()),
                    context,
                )
            dispatcher = BackgroundDispatcher(
                BackgroundScheduler(FixedClock(FIXED_NOW)), runner
            )
            scheduled_context = replace(
                context,
                schedule=OneTimeSchedule(
                    job_name="different",
                    run_at=FIXED_NOW,
                    next_run_at=FIXED_NOW,
                ),
            )
            with pytest.raises(BackgroundValidationError):
                await dispatcher.dispatch(
                    BackgroundDispatchItem(
                        BackgroundExecutionRequest(
                            job_name=job.job_name()
                        ),
                        scheduled_context,
                    )
                )

    asyncio.run(scenario())
