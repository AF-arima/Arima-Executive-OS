from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.auth.security import SecurityRateLimiter


@pytest.mark.asyncio
async def test_rate_limiter_db_failure_logs_safe_context_and_reraises(caplog) -> None:
    session = SimpleNamespace(commit=AsyncMock())
    limiter = SecurityRateLimiter(session)
    limiter.repository = SimpleNamespace(
        increment=AsyncMock(side_effect=RuntimeError("internal database detail"))
    )

    with caplog.at_level("WARNING", logger="arima.request"):
        with pytest.raises(RuntimeError):
            await limiter.enforce(
                scope="voice_transcript",
                key="actor-id",
                limit=10,
                window=timedelta(minutes=1),
                session_id="session-id",
            )

    record = next(
        record
        for record in caplog.records
        if record.name == "arima.request"
    )
    assert record.msg == "voice_rate_limit_db_failure"
    assert record.event == "voice_rate_limit_db_failure"
    assert record.session_id == "session-id"
    assert record.exception_type == "RuntimeError"
    assert "internal database detail" not in repr(record)
    session.commit.assert_not_awaited()
