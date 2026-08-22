from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import VoiceSessionRecord
from app.voice.exceptions import (
    VoiceSessionAccessDenied,
    VoiceSessionNotFound,
)
from app.voice.schemas import VoiceSession, VoiceSessionCreate
from app.voice.state import VoiceState, validate_transition

Clock = Callable[[], datetime]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class VoiceSessionStore:
    """Durable voice-session store backed by the application database."""

    def __init__(
        self,
        database: AsyncSession,
        *,
        clock: Clock = utc_now,
    ) -> None:
        self.database = database
        self.clock = clock

    async def create(
        self, data: VoiceSessionCreate, user_id: UUID
    ) -> VoiceSession:
        now = self.clock()
        session = VoiceSession(
            user_id=user_id,
            conversation_id=data.conversation_id,
            language=data.language,
            locale=data.locale,
            timezone=data.timezone,
            created_at=now,
            updated_at=now,
        )
        self.database.add(self._to_record(session))
        await self.database.commit()
        return session

    async def get(
        self,
        session_id: UUID,
        user_id: UUID,
        *,
        refresh: bool = False,
    ) -> VoiceSession:
        record = await self.database.get(
            VoiceSessionRecord,
            session_id,
            populate_existing=refresh,
        )
        if record is None:
            raise VoiceSessionNotFound("Voice session not found")
        session = self._to_schema(record)
        if session.user_id != user_id:
            raise VoiceSessionAccessDenied("Voice session access denied")
        return session

    async def rollback(self) -> None:
        await self.database.rollback()

    async def update(
        self,
        session_id: UUID,
        user_id: UUID,
        *,
        state: VoiceState | None = None,
        **values: object,
    ) -> VoiceSession:
        current = await self.get(session_id, user_id)
        if state is not None:
            validate_transition(current.state, state)
            values["state"] = state
        values["updated_at"] = self.clock()
        updated = current.model_copy(update=values)
        record = await self.database.get(VoiceSessionRecord, session_id)
        if record is None:
            raise VoiceSessionNotFound("Voice session not found")
        for name, value in values.items():
            setattr(
                record,
                name,
                value.value if isinstance(value, VoiceState) else value,
            )
        await self.database.commit()
        return updated

    async def claim_transcript(
        self,
        session_id: UUID,
        user_id: UUID,
        transcript: str,
    ) -> VoiceSession | None:
        """Atomically claim a ready session for one transcript."""
        now = self.clock()
        ready_states = tuple(
            state.value
            for state in (
                VoiceState.IDLE,
                VoiceState.LISTENING,
                VoiceState.SPEECH_DETECTED,
                VoiceState.COMPLETED,
                VoiceState.INTERRUPTED,
                VoiceState.ERROR,
            )
        )
        result = await self.database.execute(
            update(VoiceSessionRecord)
            .where(
                VoiceSessionRecord.id == session_id,
                VoiceSessionRecord.user_id == user_id,
                VoiceSessionRecord.state.in_(ready_states),
            )
            .values(
                state=VoiceState.PROCESSING.value,
                transcript=transcript,
                updated_at=now,
            )
            .execution_options(synchronize_session=False)
        )
        await self.database.commit()
        if int(getattr(result, "rowcount", 0) or 0) != 1:
            return None
        return await self.get(session_id, user_id, refresh=True)

    async def recover_stale_thinking(
        self,
        session_id: UUID,
        user_id: UUID,
        *,
        timeout: timedelta,
    ) -> bool:
        """Atomically move only an unchanged stale thinking session to error."""
        now = self.clock()
        cutoff = now - timeout
        result = await self.database.execute(
            update(VoiceSessionRecord)
            .where(
                VoiceSessionRecord.id == session_id,
                VoiceSessionRecord.user_id == user_id,
                VoiceSessionRecord.state == VoiceState.THINKING.value,
                VoiceSessionRecord.updated_at < cutoff,
            )
            .values(state=VoiceState.ERROR.value, updated_at=now)
            .execution_options(synchronize_session=False)
        )
        await self.database.commit()
        return int(getattr(result, "rowcount", 0) or 0) == 1

    @staticmethod
    def _to_record(session: VoiceSession) -> VoiceSessionRecord:
        return VoiceSessionRecord(
            id=session.session_id,
            user_id=session.user_id,
            conversation_id=session.conversation_id,
            run_id=session.run_id,
            correlation_id=session.correlation_id,
            state=session.state.value,
            language=session.language,
            locale=session.locale,
            timezone=session.timezone,
            transcript=session.transcript,
            response_text=session.response_text,
            created_at=session.created_at,
            updated_at=session.updated_at,
        )

    @staticmethod
    def _to_schema(record: VoiceSessionRecord) -> VoiceSession:
        return VoiceSession(
            session_id=record.id,
            user_id=record.user_id,
            conversation_id=record.conversation_id,
            run_id=record.run_id,
            correlation_id=record.correlation_id,
            state=VoiceState(record.state),
            language=record.language,
            locale=record.locale,
            timezone=record.timezone,
            transcript=record.transcript,
            response_text=record.response_text,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )
