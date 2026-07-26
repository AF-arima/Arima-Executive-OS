from collections.abc import Iterable

from app.background.base import BackgroundJob
from app.background.clock import Clock, SystemClock
from app.background.exceptions import (
    BackgroundConfigurationError,
    BackgroundJobNotFoundError,
)
from app.background.schemas import (
    BackgroundCapability,
    BackgroundHealth,
    BackgroundHealthState,
    BackgroundJobCategory,
    BackgroundJobType,
    BackgroundPermission,
)


class BackgroundJobRegistry:
    def __init__(
        self,
        jobs: Iterable[BackgroundJob] = (),
        *,
        clock: Clock | None = None,
    ) -> None:
        self.clock = clock or SystemClock()
        self._jobs: dict[tuple[str, str], BackgroundJob] = {}
        for job in jobs:
            self.register(job)

    def register(self, job: BackgroundJob) -> None:
        key = (job.job_name(), job.job_version())
        if key in self._jobs:
            raise BackgroundConfigurationError(
                f"Background job already registered: {key[0]}@{key[1]}"
            )
        self._jobs[key] = job

    def get(self, name: str, version: str | None = None) -> BackgroundJob:
        matches = [
            job
            for (job_name, job_version), job in self._jobs.items()
            if job_name == name
            and (version is None or version == job_version)
        ]
        if not matches:
            raise BackgroundJobNotFoundError(
                f"Background job not registered: {name}"
            )
        return max(matches, key=lambda job: job.job_version())

    def find(
        self,
        *,
        category: BackgroundJobCategory | None = None,
        capability: BackgroundCapability | None = None,
        permission: BackgroundPermission | None = None,
        trigger_type: BackgroundJobType | None = None,
        version: str | None = None,
    ) -> tuple[BackgroundJob, ...]:
        return tuple(
            job
            for job in self.all()
            if (category is None or job.job_category() is category)
            and (capability is None or capability in job.capabilities())
            and (
                permission is None
                or permission in job.required_permissions()
            )
            and (trigger_type is None or job.job_type() is trigger_type)
            and (version is None or job.job_version() == version)
        )

    def all(self) -> tuple[BackgroundJob, ...]:
        return tuple(
            sorted(self._jobs.values(), key=lambda job: job.job_name())
        )

    def __len__(self) -> int:
        return len(self._jobs)

    async def health(self) -> BackgroundHealth:
        return BackgroundHealth(
            available=bool(self._jobs),
            state=(
                BackgroundHealthState.HEALTHY
                if self._jobs
                else BackgroundHealthState.DEGRADED
            ),
            checked_at=self.clock.now(),
        )
