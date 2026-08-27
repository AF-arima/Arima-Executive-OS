import hashlib
import hmac
import logging
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.exceptions import RateLimitExceededError
from app.core.config import Settings, get_settings
from app.database.models import SecurityEvent
from app.database.repositories import RateLimitRepository

logger = logging.getLogger("arima.request")


def new_security_token() -> str:
    return secrets.token_urlsafe(48)


def hash_security_token(token: str, settings: Settings | None = None) -> str:
    active_settings = settings or get_settings()
    return hmac.new(
        active_settings.security_token_secret.get_secret_value().encode(),
        token.encode(),
        hashlib.sha256,
    ).hexdigest()


def record_security_event(
    session: AsyncSession,
    *,
    event_type: str,
    user_id: UUID | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    metadata: dict[str, object] | None = None,
) -> None:
    session.add(
        SecurityEvent(
            user_id=user_id,
            event_type=event_type,
            ip_address=ip_address,
            user_agent=user_agent,
            event_metadata=metadata or {},
        )
    )


class SecurityRateLimiter:
    def __init__(
        self,
        session: AsyncSession,
        settings: Settings | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.repository = RateLimitRepository(session)

    async def enforce(
        self,
        *,
        scope: str,
        key: str,
        limit: int,
        window: timedelta,
        session_id: str | None = None,
    ) -> None:
        now = datetime.now(UTC)
        window_seconds = max(1, int(window.total_seconds()))
        rounded = int(now.timestamp()) // window_seconds * window_seconds
        window_started_at = datetime.fromtimestamp(rounded, tz=UTC)
        try:
            count = await self.repository.increment(
                scope=scope,
                key=key[:255],
                window_started_at=window_started_at,
            )
            await self.session.commit()
        except Exception as error:
            logger.warning(
                "voice_rate_limit_db_failure",
                extra={
                    "event": "voice_rate_limit_db_failure",
                    "session_id": session_id,
                    "exception_type": type(error).__name__,
                },
            )
            raise
        if count > limit:
            retry_after = window_seconds - (int(now.timestamp()) - rounded)
            raise RateLimitExceededError(max(1, retry_after))
