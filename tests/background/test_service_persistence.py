import asyncio
from dataclasses import replace
from pathlib import Path

from alembic import command
from sqlalchemy import inspect
from sqlalchemy import create_engine

from app.background.clock import FixedClock
from app.background.factory import BackgroundJobFactory
from app.background.schemas import (
    BackgroundJobState,
    OneTimeSchedule,
)
from app.services.background_execution import BackgroundExecutionService
from tests.background.helpers import FIXED_NOW, make_context
from tests.database.helpers import sqlite_session
from tests.database.test_migrations import migration_config


def test_schedule_service_lifecycle_due_execution_and_history() -> None:
    async def scenario() -> None:
        async with sqlite_session() as session:
            clock = FixedClock(FIXED_NOW)
            registry = BackgroundJobFactory(
                clock=clock
            ).build_registry()
            job = registry.get("quant_research_summary")
            context = await make_context(session, job)
            service = BackgroundExecutionService(
                session, clock=clock, registry=registry
            )
            schedule_model = OneTimeSchedule(
                job_name=job.job_name(),
                run_at=FIXED_NOW,
                next_run_at=FIXED_NOW,
            )
            schedule = await service.create_schedule(
                schedule_model, context
            )
            assert (await service.list_due_jobs()) == [schedule]
            await service.pause_schedule(schedule.id, context)
            assert schedule.status is BackgroundJobState.PAUSED
            await service.resume_schedule(schedule.id, context)
            assert schedule.status is BackgroundJobState.SCHEDULED

            def context_factory(persisted, due_job):
                return replace(context, job=due_job)

            results = await service.execute_due_jobs(context_factory)
            assert len(results) == 1
            assert results[0].success
            assert schedule.status is BackgroundJobState.EXPIRED
            assert len(await service.get_execution_history()) == 1

    asyncio.run(scenario())


def test_cancel_schedule() -> None:
    async def scenario() -> None:
        async with sqlite_session() as session:
            clock = FixedClock(FIXED_NOW)
            registry = BackgroundJobFactory(
                clock=clock
            ).build_registry()
            job = registry.get("quant_research_summary")
            context = await make_context(session, job)
            service = BackgroundExecutionService(
                session, clock=clock, registry=registry
            )
            schedule = await service.create_schedule(
                OneTimeSchedule(
                    job_name=job.job_name(),
                    run_at=FIXED_NOW,
                    next_run_at=FIXED_NOW,
                ),
                context,
            )
            await service.cancel_schedule(schedule.id, context)
            assert schedule.status is BackgroundJobState.CANCELLED
            assert schedule.enabled is False
            assert schedule.cancelled_at == FIXED_NOW

    asyncio.run(scenario())


def test_background_migration_upgrade_indexes_and_downgrade(
    tmp_path: Path,
) -> None:
    path = tmp_path / "background.sqlite3"
    async_url = f"sqlite+aiosqlite:///{path}"
    sync_url = f"sqlite:///{path}"
    config = migration_config(async_url)
    command.upgrade(config, "head")
    engine = create_engine(sync_url)
    inspector = inspect(engine)
    tables = {
        "background_job_definitions",
        "background_job_schedules",
        "background_job_executions",
        "background_job_attempts",
        "background_job_events",
    }
    assert tables.issubset(inspector.get_table_names())
    assert {
        "ix_background_job_executions_status",
        "ix_background_job_executions_job_name",
        "ix_background_job_executions_agent_id",
        "ix_background_job_executions_user_id",
        "ix_background_job_executions_correlation_id",
    }.issubset(
        {
            item["name"]
            for item in inspector.get_indexes(
                "background_job_executions"
            )
        }
    )
    engine.dispose()
    command.downgrade(config, "20260726_0007")
    engine = create_engine(sync_url)
    assert not tables.intersection(inspect(engine).get_table_names())
    engine.dispose()
