from collections.abc import Callable, Iterable
from typing import cast

from app.background.base import BackgroundJob
from app.background.clock import Clock, SystemClock
from app.background.exceptions import BackgroundConfigurationError
from app.background.jobs import BACKGROUND_JOB_TYPES
from app.background.registry import BackgroundJobRegistry

JobBuilder = Callable[[Clock], BackgroundJob]


class BackgroundJobFactory:
    def __init__(
        self,
        *,
        clock: Clock | None = None,
        builders: Iterable[JobBuilder] | None = None,
    ) -> None:
        self.clock = clock or SystemClock()
        selected = builders or cast(
            tuple[JobBuilder, ...], BACKGROUND_JOB_TYPES
        )
        self.builders = tuple(selected)
        if not self.builders:
            raise BackgroundConfigurationError(
                "At least one background job builder is required"
            )

    def create_all(self) -> tuple[BackgroundJob, ...]:
        return tuple(builder(self.clock) for builder in self.builders)

    def build_registry(self) -> BackgroundJobRegistry:
        return BackgroundJobRegistry(self.create_all(), clock=self.clock)
