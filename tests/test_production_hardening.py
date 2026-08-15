from app.database.session import _engine_options


def test_postgresql_engine_has_explicit_pool_and_connect_timeouts() -> None:
    options = _engine_options("postgresql+asyncpg://example.invalid/arima")

    assert options["pool_timeout"] > 0
    assert options["connect_args"]["timeout"] > 0
    assert options["pool_pre_ping"] is True


def test_sqlite_engine_does_not_receive_queue_pool_options() -> None:
    assert _engine_options("sqlite+aiosqlite:///:memory:") == {}
