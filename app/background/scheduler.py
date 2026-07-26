from __future__ import annotations

from calendar import monthrange
from datetime import datetime, timedelta

from app.background.clock import Clock, SystemClock
from app.background.health import BackgroundHealthMonitor
from app.background.schemas import (
    CalendarSchedule,
    ConditionalSchedule,
    IntervalSchedule,
    OneTimeSchedule,
    RecurrenceFrequency,
    RecurringSchedule,
    ScheduleDefinition,
)


class BackgroundScheduler:
    def __init__(self, clock: Clock | None = None) -> None:
        self.clock = clock or SystemClock()
        self.monitor = BackgroundHealthMonitor(self.clock)

    def is_due(self, schedule: ScheduleDefinition) -> bool:
        now = self.clock.now()
        self.monitor.last_tick = now
        if not schedule.enabled or schedule.paused:
            return False
        if schedule.start_at is not None and now < schedule.start_at:
            return False
        if schedule.end_at is not None and now > schedule.end_at:
            return False
        if (
            schedule.maximum_runs is not None
            and schedule.run_count >= schedule.maximum_runs
        ):
            return False
        due_at = schedule.next_run_at
        if isinstance(schedule, OneTimeSchedule):
            due_at = due_at or schedule.run_at
        return due_at is not None and due_at <= now

    def next_run(
        self,
        schedule: ScheduleDefinition,
        *,
        after: datetime | None = None,
    ) -> datetime | None:
        cursor = after or self.clock.now()
        if isinstance(schedule, OneTimeSchedule):
            return schedule.run_at if cursor < schedule.run_at else None
        if isinstance(schedule, IntervalSchedule):
            return cursor + timedelta(seconds=schedule.interval_seconds)
        if isinstance(schedule, ConditionalSchedule):
            return cursor + timedelta(
                seconds=schedule.evaluation_interval_seconds
            )
        if isinstance(schedule, (RecurringSchedule, CalendarSchedule)):
            interval = (
                schedule.interval_count
                if isinstance(schedule, RecurringSchedule)
                else 1
            )
            return self._advance(cursor, schedule.frequency, interval)
        return None

    @staticmethod
    def _advance(
        value: datetime,
        frequency: RecurrenceFrequency,
        interval: int,
    ) -> datetime:
        durations = {
            RecurrenceFrequency.MINUTE: timedelta(minutes=interval),
            RecurrenceFrequency.HOURLY: timedelta(hours=interval),
            RecurrenceFrequency.DAILY: timedelta(days=interval),
            RecurrenceFrequency.WEEKLY: timedelta(weeks=interval),
            RecurrenceFrequency.CUSTOM_INTERVAL: timedelta(minutes=interval),
        }
        if frequency in durations:
            return value + durations[frequency]
        month_index = value.month - 1 + interval
        year = value.year + month_index // 12
        month = month_index % 12 + 1
        day = min(value.day, monthrange(year, month)[1])
        return value.replace(year=year, month=month, day=day)

    async def health(self):
        return self.monitor.snapshot()
