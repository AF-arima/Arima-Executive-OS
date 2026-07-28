from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from threading import RLock
from uuid import UUID

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
    """Process-local MVP session store; replaceable by a shared store later."""

    def __init__(self, *, clock: Clock = utc_now) -> None:
        self.clock = clock
        self._sessions: dict[UUID, VoiceSession] = {}
        self._lock = RLock()

    def create(self, data: VoiceSessionCreate, user_id: UUID) -> VoiceSession:
        now = self.clock()
        session = VoiceSession(
            user_id=user_id,
            language=data.language,
            locale=data.locale,
            timezone=data.timezone,
            created_at=now,
            updated_at=now,
        )
        with self._lock:
            self._sessions[session.session_id] = session
        return session

    def get(self, session_id: UUID, user_id: UUID) -> VoiceSession:
        with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise VoiceSessionNotFound("Voice session not found")
        if session.user_id != user_id:
            raise VoiceSessionAccessDenied("Voice session access denied")
        return session

    def update(
        self,
        session_id: UUID,
        user_id: UUID,
        *,
        state: VoiceState | None = None,
        **values: object,
    ) -> VoiceSession:
        current = self.get(session_id, user_id)
        if state is not None:
            validate_transition(current.state, state)
            values["state"] = state
        values["updated_at"] = self.clock()
        updated = current.model_copy(update=values)
        with self._lock:
            self._sessions[session_id] = updated
        return updated
