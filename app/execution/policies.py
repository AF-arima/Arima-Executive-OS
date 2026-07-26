from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TypeVar

from pydantic import JsonValue

from app.execution.exceptions import (
    ExecutionTimeout,
    RetryExhausted,
)

ResultT = TypeVar("ResultT")
DecimalLike = int | float


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    max_attempts: int = 1
    backoff_base_ms: int = 0
    backoff_factor: DecimalLike = 1

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        if self.backoff_base_ms < 0:
            raise ValueError("backoff_base_ms cannot be negative")
        if float(self.backoff_factor) < 1:
            raise ValueError("backoff_factor must be at least one")

    def should_retry(self, *, attempt: int, retryable: bool) -> bool:
        return retryable and attempt < self.max_attempts

    def backoff_metadata(self, attempt: int) -> dict[str, JsonValue]:
        delay = int(
            self.backoff_base_ms
            * (float(self.backoff_factor) ** max(0, attempt - 1))
        )
        return {
            "attempt": attempt,
            "max_attempts": self.max_attempts,
            "backoff_ms": delay,
            "sleep_performed": False,
        }
class RetryExecutor:
    def __init__(self, policy: RetryPolicy) -> None:
        self.policy = policy

    async def run(
        self,
        operation: Callable[[int], Awaitable[ResultT]],
    ) -> ResultT:
        last_error: Exception | None = None
        for attempt in range(1, self.policy.max_attempts + 1):
            try:
                return await operation(attempt)
            except Exception as error:
                last_error = error
                retryable = bool(getattr(error, "retryable", False))
                if not retryable:
                    raise
                if not self.policy.should_retry(
                    attempt=attempt,
                    retryable=retryable,
                ):
                    raise RetryExhausted(attempt, error) from error
        if last_error is None:
            raise RuntimeError("Retry executor did not run")
        raise RetryExhausted(self.policy.max_attempts, last_error)


@dataclass(frozen=True, slots=True)
class TimeoutPolicy:
    max_duration_ms: int

    def __post_init__(self) -> None:
        if self.max_duration_ms < 0:
            raise ValueError("max_duration_ms cannot be negative")

    def exceeded(self, elapsed_ms: int) -> bool:
        if elapsed_ms < 0:
            raise ValueError("elapsed_ms cannot be negative")
        return elapsed_ms > self.max_duration_ms

    def ensure_within_limit(self, elapsed_ms: int) -> None:
        if self.exceeded(elapsed_ms):
            raise ExecutionTimeout(
                f"Execution exceeded {self.max_duration_ms}ms"
            )

    def metadata(self) -> dict[str, JsonValue]:
        return {
            "max_duration_ms": self.max_duration_ms,
            "timer_active": False,
        }
