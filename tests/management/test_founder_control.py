import asyncio
from collections.abc import Iterator

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.database.models import AuditEntity, AuditLog, DataFeedObservation
from tests.auth.conftest import AuthTestContext
from tests.auth.helpers import (
    bearer,
    csrf_headers,
    grant_role,
    login_user,
    register_user,
)


@pytest.fixture
def founder_allowlist(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("FOUNDER_CONTROL_EMAILS", "founder@example.com")
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


def test_founder_control_requires_server_side_founder_authorization(
    management_context: AuthTestContext,
    founder_allowlist: None,
) -> None:
    normal = register_user(management_context, "normal@example.com")
    allowlisted_manager = register_user(
        management_context,
        "founder@example.com",
    )
    other_admin = register_user(management_context, "admin@example.com")
    grant_role(management_context, "admin@example.com", "administrator")
    grant_role(management_context, "founder@example.com", "administrator")

    unauthenticated = management_context.client.get(
        "/api/v1/admin/founder/system-health"
    )
    normal_response = management_context.client.get(
        "/api/v1/admin/founder/system-health",
        headers=bearer(
            login_user(management_context, "normal@example.com")[
                "access_token"
            ]
        ),
    )

    # Revert the allowlisted account to the default manager role to prove an
    # allowlist entry is not a role-elevation path.
    grant_role(management_context, "founder@example.com", "manager")
    allowlisted_manager_response = management_context.client.get(
        "/api/v1/admin/founder/system-health",
        headers=bearer(
            login_user(management_context, "founder@example.com")[
                "access_token"
            ]
        ),
    )
    other_admin_response = management_context.client.get(
        "/api/v1/admin/founder/system-health",
        headers=bearer(
            login_user(management_context, "admin@example.com")[
                "access_token"
            ]
        ),
    )

    grant_role(management_context, "founder@example.com", "administrator")
    founder_response = management_context.client.get(
        "/api/v1/admin/founder/system-health",
        headers=bearer(
            login_user(management_context, "founder@example.com")[
                "access_token"
            ]
        ),
    )

    assert normal["email"] == "normal@example.com"
    assert allowlisted_manager["email"] == "founder@example.com"
    assert other_admin["email"] == "admin@example.com"
    assert unauthenticated.status_code == 401
    assert normal_response.status_code == 403
    assert allowlisted_manager_response.status_code == 403
    assert other_admin_response.status_code == 403
    assert founder_response.status_code == 200
    body = founder_response.json()
    assert {component["key"] for component in body["components"]} >= {
        "backend",
        "database",
        "email_configuration",
        "voice",
    }
    assert "development-only-security-token-secret-change-me" not in str(body)


def test_founder_manual_evidence_is_csrf_protected_and_audited(
    management_context: AuthTestContext,
    founder_allowlist: None,
) -> None:
    register_user(management_context, "founder@example.com")
    grant_role(management_context, "founder@example.com", "administrator")
    access_token = login_user(management_context, "founder@example.com")[
        "access_token"
    ]
    endpoint = "/api/v1/admin/founder/data-feeds/quant_research/observations"
    payload = {
        "source": "Internal research memorandum 2026-08-11",
        "observed_at": "2026-08-11T12:00:00+00:00",
        "expires_at": "2026-08-12T12:00:00+00:00",
        "notes": "No ranking or return values recorded.",
    }

    missing_csrf = management_context.client.post(
        endpoint,
        headers=bearer(access_token),
        json=payload,
    )
    created = management_context.client.post(
        endpoint,
        headers={**bearer(access_token), **csrf_headers(management_context)},
        json=payload,
    )
    unknown_feed = management_context.client.post(
        "/api/v1/admin/founder/data-feeds/not-a-feed/observations",
        headers={**bearer(access_token), **csrf_headers(management_context)},
        json=payload,
    )

    assert missing_csrf.status_code == 403
    assert created.status_code == 201
    assert unknown_feed.status_code == 404
    body = created.json()
    assert body["feed_key"] == "quant_research"
    assert body["status"] == "manual"
    assert body["entered_by"] == "founder@example.com"
    assert body["correlation_id"] == created.headers["X-Correlation-ID"]

    async def assert_persisted() -> None:
        async with management_context.session_factory() as session:
            observation = await session.scalar(select(DataFeedObservation))
            audit = await session.scalar(
                select(AuditLog).where(
                    AuditLog.entity == AuditEntity.DATA_FEED_OBSERVATION
                )
            )
            assert observation is not None
            assert audit is not None
            assert audit.entity_id == observation.id

    asyncio.run(assert_persisted())


def test_founder_data_feed_state_uses_provenance_not_simulated_results(
    management_context: AuthTestContext,
    founder_allowlist: None,
) -> None:
    register_user(management_context, "founder@example.com")
    grant_role(management_context, "founder@example.com", "administrator")
    headers = bearer(
        login_user(management_context, "founder@example.com")["access_token"]
    )

    response = management_context.client.get(
        "/api/v1/admin/founder/data-feeds",
        headers=headers,
    )

    assert response.status_code == 200
    feeds = {feed["key"]: feed for feed in response.json()["feeds"]}
    assert feeds["quant_research"]["status"] == "unavailable"
    assert feeds["quant_research"]["freshness"] == "unavailable"
    assert feeds["quant_research"]["last_updated_at"] is None
    assert feeds["quant_research"]["manual_entry_supported"] is True
