from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.background.clock import Clock, SystemClock
from app.background.schemas import BackgroundHealth, BackgroundHealthState


@dataclass(slots=True)
class BackgroundHealthMonitor:
    clock: Clock
    last_tick: datetime | None = None
    last_dispatch: datetime | None = None
    last_success: datetime | None = None
    last_failure: datetime | None = None
    active_jobs: int = 0
    failed_jobs: int = 0
    blocked_jobs: int = 0

    def __init__(self, clock: Clock | None = None) -> None:
        self.clock = clock or SystemClock()
        self.last_tick = None
        self.last_dispatch = None
        self.last_success = None
        self.last_failure = None
        self.active_jobs = 0
        self.failed_jobs = 0
        self.blocked_jobs = 0

    def snapshot(self) -> BackgroundHealth:
        return BackgroundHealth(
            available=True,
            state=BackgroundHealthState.HEALTHY,
            checked_at=self.clock.now(),
            last_tick=self.last_tick,
            last_dispatch=self.last_dispatch,
            last_success=self.last_success,
            last_failure=self.last_failure,
            active_jobs=self.active_jobs,
            failed_jobs=self.failed_jobs,
            blocked_jobs=self.blocked_jobs,
        )
