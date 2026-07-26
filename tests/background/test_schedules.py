from datetime import timedelta

from app.background.clock import FixedClock
from app.background.scheduler import BackgroundScheduler
from app.background.schemas import (
    CalendarSchedule,
    ConditionalSchedule,
    IntervalSchedule,
    OneTimeSchedule,
    RecurrenceFrequency,
    RecurringSchedule,
)
from tests.background.helpers import FIXED_NOW


def test_all_schedule_types_and_due_status() -> None:
    scheduler = BackgroundScheduler(FixedClock(FIXED_NOW))
    schedules = (
        OneTimeSchedule(
            job_name="job",
            run_at=FIXED_NOW,
            next_run_at=FIXED_NOW,
        ),
        RecurringSchedule(
            job_name="job",
            frequency=RecurrenceFrequency.DAILY,
            next_run_at=FIXED_NOW,
        ),
        IntervalSchedule(
            job_name="job",
            interval_seconds=60,
            next_run_at=FIXED_NOW,
        ),
        CalendarSchedule(
            job_name="job",
            frequency=RecurrenceFrequency.WEEKLY,
            next_run_at=FIXED_NOW,
        ),
        ConditionalSchedule(
            job_name="job",
            condition={"field": "value"},
            next_run_at=FIXED_NOW,
        ),
    )
    assert len(schedules) == 5
    assert all(scheduler.is_due(schedule) for schedule in schedules)
    assert not scheduler.is_due(
        schedules[1].model_copy(update={"paused": True})
    )


def test_recurrence_contract_calculation() -> None:
    scheduler = BackgroundScheduler(FixedClock(FIXED_NOW))
    minute = RecurringSchedule(
        job_name="job", frequency=RecurrenceFrequency.MINUTE
    )
    monthly = RecurringSchedule(
        job_name="job", frequency=RecurrenceFrequency.MONTHLY
    )
    interval = IntervalSchedule(job_name="job", interval_seconds=90)
    assert scheduler.next_run(minute) == FIXED_NOW + timedelta(minutes=1)
    monthly_next = scheduler.next_run(monthly)
    assert monthly_next is not None
    assert monthly_next.month == 8
    assert scheduler.next_run(interval) == FIXED_NOW + timedelta(seconds=90)
