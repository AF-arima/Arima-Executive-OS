from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from app.integrations.schemas import (
    ConnectorHealth,
    ConnectorHealthState,
)


@dataclass(slots=True)
class ConnectorHealthMonitor:
    last_successful_execution: datetime | None = None
    last_failed_execution: datetime | None = None
    latency_ms: float | None = None

    def record_success(self, latency_ms: float) -> None:
        self.last_successful_execution = datetime.now(timezone.utc)
        self.latency_ms = max(latency_ms, 0)

    def record_failure(self, latency_ms: float) -> None:
        self.last_failed_execution = datetime.now(timezone.utc)
        self.latency_ms = max(latency_ms, 0)

    def snapshot(self) -> ConnectorHealth:
        return ConnectorHealth(
            available=True,
            latency_ms=self.latency_ms,
            last_successful_execution=self.last_successful_execution,
            last_failed_execution=self.last_failed_execution,
            state=ConnectorHealthState.HEALTHY,
            checked_at=datetime.now(timezone.utc),
            detail="Deterministic mock connector",
        )
