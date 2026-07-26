from __future__ import annotations

from dataclasses import dataclass

from app.background.exceptions import BackgroundConfigurationError


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    maximum_attempts: int = 1
    initial_delay_seconds: float = 0
    maximum_delay_seconds: float = 60
    backoff_multiplier: float = 2
    retryable_exceptions: tuple[type[Exception], ...] = (Exception,)
    non_retryable_exceptions: tuple[type[Exception], ...] = ()
    jitter: bool = False

    def __post_init__(self) -> None:
        if self.maximum_attempts < 1:
            raise BackgroundConfigurationError(
                "Retry attempts must be positive"
            )
        if min(self.initial_delay_seconds, self.maximum_delay_seconds) < 0:
            raise BackgroundConfigurationError(
                "Retry delays cannot be negative"
            )
        if self.backoff_multiplier < 1:
            raise BackgroundConfigurationError(
                "Backoff multiplier must be at least one"
            )

    def should_retry(self, error: Exception, attempt: int) -> bool:
        return (
            attempt < self.maximum_attempts
            and not isinstance(error, self.non_retryable_exceptions)
            and isinstance(error, self.retryable_exceptions)
        )

    def delay_for(self, attempt: int) -> float:
        delay = self.initial_delay_seconds * (
            self.backoff_multiplier ** max(attempt - 1, 0)
        )
        return min(delay, self.maximum_delay_seconds)


@dataclass(frozen=True, slots=True)
class TimeoutPolicy:
    execution_timeout_seconds: float = 300
    cancellation_timeout_seconds: float = 30
    heartbeat_timeout_seconds: float = 60

    def __post_init__(self) -> None:
        if min(
            self.execution_timeout_seconds,
            self.cancellation_timeout_seconds,
            self.heartbeat_timeout_seconds,
        ) <= 0:
            raise BackgroundConfigurationError(
                "Timeout values must be positive"
            )
