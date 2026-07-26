from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.background.schemas import BackgroundLifecycleRecord
from app.database.models import (
    AuditAction,
    AuditEntity,
    BackgroundJobEvent,
)
from app.services.audit import record_audit


class BackgroundLifecycleLogSink(Protocol):
    async def record(self, lifecycle: BackgroundLifecycleRecord) -> None: ...


class InMemoryBackgroundLifecycleLog:
    def __init__(self) -> None:
        self._records: list[BackgroundLifecycleRecord] = []

    async def record(self, lifecycle: BackgroundLifecycleRecord) -> None:
        self._records.append(lifecycle)

    def records(self) -> Sequence[BackgroundLifecycleRecord]:
        return tuple(self._records)


class DatabaseBackgroundLifecycleLog:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(self, lifecycle: BackgroundLifecycleRecord) -> None:
        self.session.add(
            BackgroundJobEvent(
                execution_id=lifecycle.execution_id,
                schedule_id=lifecycle.schedule_id,
                job_name=lifecycle.job,
                correlation_id=lifecycle.correlation_id,
                from_state=lifecycle.from_state,
                to_state=lifecycle.to_state,
                event_type="state_transition",
                event_metadata={
                    "user_id": str(lifecycle.user_id),
                    "agent_id": str(lifecycle.agent_id),
                    "trigger": lifecycle.trigger.value,
                    "attempt": lifecycle.attempt,
                    "duration_ms": lifecycle.duration_ms,
                    "result": lifecycle.result,
                    "approval_outcome": lifecycle.approval_outcome.value,
                    "permission_outcome": lifecycle.permission_outcome,
                },
                timestamp=lifecycle.timestamp,
            )
        )
        record_audit(
            self.session,
            actor_id=lifecycle.user_id,
            action=(
                AuditAction.COMPLETE
                if lifecycle.to_state.value == "succeeded"
                else AuditAction.STATUS_CHANGE
            ),
            entity=AuditEntity.AUTOMATION,
            entity_id=lifecycle.correlation_id,
        )
