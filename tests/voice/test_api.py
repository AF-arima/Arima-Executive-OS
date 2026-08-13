from tests.auth.helpers import bearer, login_user, register_user
from tests.management.conftest import management_context

__all__ = ["management_context"]

PRODUCTION_FRONTEND_ORIGIN = (
    "https://aryan-portfolio.ea-arima7576.workers.dev"
)


def test_production_frontend_voice_session_preflight(
    management_context,
) -> None:
    response = management_context.client.options(
        "/api/v1/voice/sessions",
        headers={
            "Origin": PRODUCTION_FRONTEND_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": (
                "authorization,content-type,x-csrf-token"
            ),
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == (
        PRODUCTION_FRONTEND_ORIGIN
    )
    assert response.headers["access-control-allow-credentials"] == "true"
    assert "POST" in response.headers["access-control-allow-methods"]


def test_voice_routes_require_authentication(management_context) -> None:
    response = management_context.client.post(
        "/api/v1/voice/sessions", json={}
    )
    assert response.status_code == 401


def test_voice_session_command_and_health_routes(management_context) -> None:
    register_user(management_context, "voice@example.com")
    tokens = login_user(management_context, "voice@example.com")
    headers = bearer(tokens["access_token"])
    created = management_context.client.post(
        "/api/v1/voice/sessions",
        json={},
        headers=headers,
    )
    assert created.status_code == 201
    session_id = created.json()["session_id"]
    fetched = management_context.client.get(
        f"/api/v1/voice/sessions/{session_id}",
        headers=headers,
    )
    assert fetched.status_code == 200
    response = management_context.client.post(
        f"/api/v1/voice/sessions/{session_id}/transcript",
        json={"transcript": "Open Portfolio"},
        headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["navigation_action"]["path"] == "/portfolio-lab"
    experience_events = response.json()["experience_events"]
    assert experience_events
    assert all(event["session_id"] == session_id for event in experience_events)
    assert all("correlation_id" in event for event in experience_events)
    interrupted = management_context.client.post(
        f"/api/v1/voice/sessions/{session_id}/interrupt",
        headers=headers,
    )
    assert interrupted.status_code == 200
    cancelled = management_context.client.post(
        f"/api/v1/voice/sessions/{session_id}/cancel",
        headers=headers,
    )
    assert cancelled.status_code == 200
    health = management_context.client.get(
        "/api/v1/voice/health", headers=headers
    )
    assert health.status_code == 200
    assert health.json()["provider_neutral"] is True
    assert health.json()["session_store"] == "postgresql"


def test_voice_session_ownership_is_preserved_across_requests(
    management_context,
) -> None:
    register_user(management_context, "voice-owner@example.com")
    register_user(management_context, "voice-other@example.com")
    owner = login_user(management_context, "voice-owner@example.com")
    other = login_user(management_context, "voice-other@example.com")

    created = management_context.client.post(
        "/api/v1/voice/sessions",
        json={},
        headers=bearer(owner["access_token"]),
    )
    assert created.status_code == 201
    session_id = created.json()["session_id"]

    denied = management_context.client.get(
        f"/api/v1/voice/sessions/{session_id}",
        headers=bearer(other["access_token"]),
    )
    assert denied.status_code == 403

    fetched = management_context.client.get(
        f"/api/v1/voice/sessions/{session_id}",
        headers=bearer(owner["access_token"]),
    )
    assert fetched.status_code == 200
    assert fetched.json()["user_id"] == created.json()["user_id"]
