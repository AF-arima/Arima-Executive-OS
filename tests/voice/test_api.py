import asyncio
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select

from app.database.models import AgentConversation, VoiceSessionRecord, WorkspaceAgentGrant
from app.database.repositories import UserRepository, WorkspaceRepository
from app.database.repositories.agent import AgentDefinitionRepository
from app.intelligence.access import AgentGrantService
from app.core.config import get_settings
from app.services.agent_bootstrap import bootstrap_agent_platform
from app.voice.session import VoiceSessionStore
from app.voice.state import VoiceState
from app.voice.exceptions import VoiceExecutionTimeout, VoiceProviderUnavailable
from tests.auth.helpers import bearer, login_user, register_user
from tests.management.conftest import management_context

__all__ = ["management_context"]

CONFIGURED_FRONTEND_ORIGIN = "http://localhost:3000"


def configure_default_voice_agent(
    management_context,
    email: str,
    *,
    grant: bool,
) -> None:
    async def configure() -> None:
        async with management_context.session_factory() as session:
            user = await UserRepository(session).get_by_email(email)
            assert user is not None
            agent = await bootstrap_agent_platform(session, created_by_id=user.id)
            if grant:
                workspace = await WorkspaceRepository(session).get_by_owner(user.id)
                assert workspace is not None
                await AgentGrantService(session).grant(
                    workspace_id=workspace.id,
                    agent_id=agent.agent.id,
                    actor=user,
                )

    asyncio.run(configure())


def voice_record_counts(management_context, email: str) -> tuple[int, int]:
    async def count() -> tuple[int, int]:
        async with management_context.session_factory() as session:
            user = await UserRepository(session).get_by_email(email)
            assert user is not None
            conversations = await session.scalar(
                select(func.count(AgentConversation.id)).where(
                    AgentConversation.owner_id == user.id
                )
            )
            sessions = await session.scalar(
                select(func.count(VoiceSessionRecord.id)).where(
                    VoiceSessionRecord.user_id == user.id
                )
            )
            return int(conversations or 0), int(sessions or 0)

    return asyncio.run(count())


def test_configured_frontend_voice_session_preflight(
    management_context,
) -> None:
    response = management_context.client.options(
        "/api/v1/voice/sessions",
        headers={
            "Origin": CONFIGURED_FRONTEND_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": (
                "authorization,content-type,x-csrf-token"
            ),
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == (
        CONFIGURED_FRONTEND_ORIGIN
    )
    assert response.headers["access-control-allow-credentials"] == "true"
    assert "POST" in response.headers["access-control-allow-methods"]

    stale_origin = management_context.client.options(
        "/api/v1/voice/sessions",
        headers={
            "Origin": "https://aryan-portfolio.ea-arima7576.workers.dev",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert "access-control-allow-origin" not in stale_origin.headers


def test_voice_routes_require_authentication(management_context) -> None:
    response = management_context.client.post(
        "/api/v1/voice/sessions", json={}
    )
    assert response.status_code == 401


def test_voice_transcripts_are_rate_limited_per_authenticated_user(
    management_context,
) -> None:
    register_user(management_context, "voice-rate-limit@example.com")
    configure_default_voice_agent(
        management_context, "voice-rate-limit@example.com", grant=True
    )
    tokens = login_user(management_context, "voice-rate-limit@example.com")
    headers = bearer(tokens["access_token"])
    created = management_context.client.post(
        "/api/v1/voice/sessions", json={}, headers=headers
    )
    assert created.status_code == 201
    session_id = created.json()["session_id"]
    settings = get_settings()
    original_limit = settings.voice_transcript_rate_limit_per_minute
    settings.voice_transcript_rate_limit_per_minute = 1
    try:
        first = management_context.client.post(
            f"/api/v1/voice/sessions/{session_id}/transcript",
            json={"transcript": "Open Portfolio"},
            headers=headers,
        )
        second = management_context.client.post(
            f"/api/v1/voice/sessions/{session_id}/transcript",
            json={"transcript": "Open Portfolio"},
            headers=headers,
        )
    finally:
        settings.voice_transcript_rate_limit_per_minute = original_limit

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json() == {"detail": "Too many requests. Try again later."}
    assert int(second.headers["Retry-After"]) > 0


def test_active_transcript_returns_conflict_instead_of_internal_error(
    management_context,
) -> None:
    email = "voice-active-transcript@example.com"
    register_user(management_context, email)
    configure_default_voice_agent(management_context, email, grant=True)
    tokens = login_user(management_context, email)
    headers = bearer(tokens["access_token"])
    created = management_context.client.post(
        "/api/v1/voice/sessions", json={}, headers=headers
    )
    assert created.status_code == 201
    session_id = created.json()["session_id"]

    async def mark_thinking() -> None:
        async with management_context.session_factory() as session:
            record = await session.get(VoiceSessionRecord, UUID(session_id))
            assert record is not None
            record.state = VoiceState.THINKING.value
            record.updated_at = datetime.now(timezone.utc)
            await session.commit()

    asyncio.run(mark_thinking())
    response = management_context.client.post(
        f"/api/v1/voice/sessions/{session_id}/transcript",
        json={"transcript": "Open Portfolio"},
        headers=headers,
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": "A transcript is already being processed for this session"
    }


def test_provider_timeout_returns_gateway_timeout(
    management_context, monkeypatch
) -> None:
    email = "voice-provider-timeout@example.com"
    register_user(management_context, email)
    configure_default_voice_agent(management_context, email, grant=True)
    tokens = login_user(management_context, email)
    headers = bearer(tokens["access_token"])
    created = management_context.client.post(
        "/api/v1/voice/sessions", json={}, headers=headers
    )
    assert created.status_code == 201

    class TimeoutGateway:
        async def handle_transcript(
            self,
            session_id,
            transcript,
            actor,
            correlation_id=None,
            boundary_trace=None,
        ):
            del session_id, transcript, actor, correlation_id, boundary_trace
            raise VoiceExecutionTimeout("provider timed out")

    monkeypatch.setattr(
        "app.api.v1.routes.voice.VoiceGatewayFactory.create",
        lambda self: TimeoutGateway(),
    )
    response = management_context.client.post(
        f"/api/v1/voice/sessions/{created.json()['session_id']}/transcript",
        json={"transcript": "Analyse this decision"},
        headers=headers,
    )

    assert response.status_code == 504
    assert response.json() == {"detail": "provider timed out"}


def test_provider_unavailability_returns_service_unavailable(
    management_context, monkeypatch
) -> None:
    email = "voice-provider-unavailable@example.com"
    register_user(management_context, email)
    configure_default_voice_agent(management_context, email, grant=True)
    tokens = login_user(management_context, email)
    headers = bearer(tokens["access_token"])
    created = management_context.client.post(
        "/api/v1/voice/sessions", json={}, headers=headers
    )
    assert created.status_code == 201

    class UnavailableGateway:
        async def handle_transcript(
            self,
            session_id,
            transcript,
            actor,
            correlation_id=None,
            boundary_trace=None,
        ):
            del session_id, transcript, actor, correlation_id, boundary_trace
            raise VoiceProviderUnavailable("provider unavailable")

    monkeypatch.setattr(
        "app.api.v1.routes.voice.VoiceGatewayFactory.create",
        lambda self: UnavailableGateway(),
    )
    response = management_context.client.post(
        f"/api/v1/voice/sessions/{created.json()['session_id']}/transcript",
        json={"transcript": "Analyse this decision"},
        headers=headers,
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "provider unavailable"}


def test_unclaimed_transcript_returns_conflict_without_state_fallback(
    management_context, monkeypatch
) -> None:
    email = "voice-unclaimed-transcript@example.com"
    register_user(management_context, email)
    configure_default_voice_agent(management_context, email, grant=True)
    tokens = login_user(management_context, email)
    headers = bearer(tokens["access_token"])
    created = management_context.client.post(
        "/api/v1/voice/sessions", json={}, headers=headers
    )
    assert created.status_code == 201
    session_id = created.json()["session_id"]

    async def do_not_claim(self, session_id, user_id, transcript):
        del self, session_id, user_id, transcript
        return None

    monkeypatch.setattr(VoiceSessionStore, "claim_transcript", do_not_claim)
    response = management_context.client.post(
        f"/api/v1/voice/sessions/{session_id}/transcript",
        json={"transcript": "Open Portfolio"},
        headers=headers,
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": "A transcript is already being processed for this session"
    }


def test_voice_session_command_and_health_routes(management_context) -> None:
    register_user(management_context, "voice@example.com")
    configure_default_voice_agent(
        management_context, "voice@example.com", grant=True
    )
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
    configure_default_voice_agent(
        management_context, "voice-owner@example.com", grant=True
    )
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


def test_missing_default_agent_grant_returns_cors_protected_403_without_records(
    management_context,
) -> None:
    email = "voice-no-grant@example.com"
    register_user(management_context, email)
    configure_default_voice_agent(management_context, email, grant=False)
    tokens = login_user(management_context, email)
    before = voice_record_counts(management_context, email)
    response = management_context.client.post(
        "/api/v1/voice/sessions",
        json={},
        headers={
            **bearer(tokens["access_token"]),
            "Origin": CONFIGURED_FRONTEND_ORIGIN,
        },
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "Voice AI authorization denied"}
    assert response.headers["access-control-allow-origin"] == (
        CONFIGURED_FRONTEND_ORIGIN
    )
    assert voice_record_counts(management_context, email) == before


def test_authorized_default_agent_grant_creates_voice_session(
    management_context,
) -> None:
    email = "voice-authorized-default@example.com"
    register_user(management_context, email)
    configure_default_voice_agent(management_context, email, grant=True)
    tokens = login_user(management_context, email)

    response = management_context.client.post(
        "/api/v1/voice/sessions",
        json={},
        headers=bearer(tokens["access_token"]),
    )

    assert response.status_code == 201


def test_fresh_registration_receives_default_agent_grant_for_voice_session(
    management_context,
) -> None:
    register_user(management_context, "platform-bootstrap@example.com")
    configure_default_voice_agent(
        management_context,
        "platform-bootstrap@example.com",
        grant=False,
    )

    email = "voice-fresh-onboarding@example.com"
    user = register_user(management_context, email)
    tokens = login_user(management_context, email)

    async def assert_grant() -> None:
        async with management_context.session_factory() as session:
            account = await UserRepository(session).get_by_email(email)
            assert account is not None
            workspace = await WorkspaceRepository(session).get_by_owner(account.id)
            assert workspace is not None
            agent = await AgentDefinitionRepository(session).get_active_default()
            assert agent is not None
            grant = await session.scalar(
                select(WorkspaceAgentGrant).where(
                    WorkspaceAgentGrant.workspace_id == workspace.id,
                    WorkspaceAgentGrant.agent_id == agent.id,
                )
            )
            assert grant is not None

    asyncio.run(assert_grant())
    response = management_context.client.post(
        "/api/v1/voice/sessions",
        json={},
        headers=bearer(tokens["access_token"]),
    )
    assert response.status_code == 201
    assert response.json()["user_id"] == user["id"]
    assert response.json()["conversation_id"] is not None
