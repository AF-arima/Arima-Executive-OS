from datetime import UTC, datetime, timedelta

from app.integrations.microsoft import _utc_datetime


def test_sqlite_naive_oauth_expiry_normalizes_to_utc():
    naive_expiry = datetime.now(UTC).replace(tzinfo=None) + timedelta(minutes=5)

    normalized = _utc_datetime(naive_expiry)

    assert normalized.tzinfo is UTC
    assert normalized > _utc_datetime(datetime.now(UTC))
