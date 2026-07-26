from collections.abc import Sequence
from typing import Protocol

from app.orchestration.health import HealthContract
from app.orchestration.schemas import TelemetryRecord


class TelemetrySink(Protocol):
    async def record(self, telemetry: TelemetryRecord) -> None: ...


class InMemoryTelemetrySink:
    def __init__(self) -> None:
        self._records: list[TelemetryRecord] = []

    async def record(self, telemetry: TelemetryRecord) -> None:
        self._records.append(telemetry)

    def records(self) -> Sequence[TelemetryRecord]:
        return tuple(self._records)


class OrchestrationTelemetry(HealthContract):
    component_name = "telemetry"

    def __init__(self, sink: TelemetrySink | None = None) -> None:
        self.sink = sink or InMemoryTelemetrySink()

    async def record(self, telemetry: TelemetryRecord) -> None:
        await self.sink.record(telemetry)
