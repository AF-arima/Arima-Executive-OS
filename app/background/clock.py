from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone


class Clock(ABC):
    @abstractmethod
    def now(self) -> datetime: ...


class SystemClock(Clock):
    def now(self) -> datetime:
        return datetime.now(timezone.utc)


@dataclass(slots=True)
class FixedClock(Clock):
    current: datetime

    def __post_init__(self) -> None:
        if self.current.tzinfo is None:
            raise ValueError("Fixed clock must be timezone-aware")

    def now(self) -> datetime:
        return self.current

    def set(self, value: datetime) -> None:
        if value.tzinfo is None:
            raise ValueError("Fixed clock must be timezone-aware")
        self.current = value
