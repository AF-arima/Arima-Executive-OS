import asyncio
from types import SimpleNamespace
from uuid import UUID

import pytest
from starlette.requests import Request

from app.api.v1.routes.voice import (
    _emit_pre_handler_event,
    _safe_exception_type,
    _trace_voice_database,
    _trace_voice_user,
)
from app.auth.exceptions import InvalidTokenError

SESSION_ID = UUID("7a338ffb-493c-4a6c-a941-79fe75aa6229")


def _request() -> Request:
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/voice",
            "app": SimpleNamespace(dependency_overrides={}),
        }
    )
    request.state.correlation_id = "a259bbc4-5104-4128-a1da-120772696e3e"
    return request


def test_pre_handler_event_is_allowlisted_and_redacted(caplog) -> None:
    caplog.set_level("INFO", logger="arima.request")

    _emit_pre_handler_event(
        "session_dependency_failure",
        _request(),
        SESSION_ID,
        error=ValueError("secret prompt and response must not be logged"),
    )

    record = caplog.records[-1]
    assert record.msg == "session_dependency_failure"
    assert record.event == "session_dependency_failure"
    assert record.session_id == str(SESSION_ID)
    assert record.correlation_id == "a259bbc4-5104-4128-a1da-120772696e3e"
    assert record.exception_type == "unknown"
    assert "secret prompt" not in record.getMessage()
    assert "secret prompt" not in repr(record.__dict__)


def test_database_dependency_emits_success(monkeypatch, caplog) -> None:
    async def fake_get_session():
        yield SimpleNamespace()

    monkeypatch.setattr("app.api.v1.routes.voice.get_session", fake_get_session)
    caplog.set_level("INFO", logger="arima.request")

    async def collect() -> None:
        async for _ in _trace_voice_database(_request(), SESSION_ID):
            pass

    asyncio.run(collect())
    assert [record.event for record in caplog.records[-2:]] == [
        "session_dependency_success",
        "db_session_success",
    ]


def test_auth_dependency_emits_failure_without_exception_text(monkeypatch, caplog) -> None:
    async def fail_get_current_user(database, token):
        del database, token
        raise InvalidTokenError("token value must not be logged")

    async def should_not_run(current_user):
        return current_user

    monkeypatch.setattr(
        "app.api.v1.routes.voice.get_current_user", fail_get_current_user
    )
    monkeypatch.setattr(
        "app.api.v1.routes.voice.get_current_active_user", should_not_run
    )
    caplog.set_level("INFO", logger="arima.request")

    async def invoke() -> None:
        with pytest.raises(InvalidTokenError):
            await _trace_voice_user(_request(), SESSION_ID, SimpleNamespace(), "token")

    asyncio.run(invoke())
    record = caplog.records[-1]
    assert record.event == "voice_auth_failure"
    assert record.exception_type == "InvalidTokenError"
    assert "token value" not in repr(record.__dict__)


def test_unknown_exception_type_is_normalized() -> None:
    assert _safe_exception_type(RuntimeError("hidden")) == "unknown"
