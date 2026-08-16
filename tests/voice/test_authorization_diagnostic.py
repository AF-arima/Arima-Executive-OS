import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.database.models import (
    AgentConversation,
    AgentDefinition,
    AgentRun,
    AgentRunStatus,
    AgentStatus,
    AuditAction,
    AuditEntity,
    AuditLog,
    ConversationPriority,
    ConversationStatus,
    User,
    VoiceSessionRecord,
    Workspace,
)
from app.intelligence.access import AgentGrantService
from app.voice.state import VoiceState
from tests.auth.conftest import AuthTestContext
from tests.auth.helpers import bearer, grant_role, login_user, register_user

pytest_plugins = ("tests.management.conftest",)


@pytest.fixture
def founder_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[None]:
    monkeypatch.setenv("FOUNDER_CONTROL_EMAILS", "founder@example.com")
    get_settings.cache_clear()
    try:
        yield
    finally:
        get_settings.cache_clear()


async def _create_voice_session(
    context: AuthTestContext,
    email: str,
    *,
    grant: bool = False,
    run_workspace_id: UUID | None = None,
) -> UUID:
    async with context.session_factory() as session:
        actor = await session.scalar(select(User).where(User.email == email))
        assert actor is not None
        workspace = await session.scalar(
            select(Workspace).where(Workspace.owner_id == actor.id)
        )
        assert workspace is not None
        agent = AgentDefinition(
            slug=f"diagnostic-{uuid4()}",
            name="Diagnostic Agent",
            description=None,
            system_instructions="Diagnostic test agent",
            status=AgentStatus.ACTIVE,
            version=1,
            is_default=False,
            created_by_id=actor.id,
        )
        session.add(agent)
        await session.flush()
        conversation = AgentConversation(
            agent_id=agent.id,
            owner_id=actor.id,
            title="Diagnostic conversation",
            status=ConversationStatus.ACTIVE,
            priority=ConversationPriority.NORMAL,
            pinned=False,
            metadata_={"workspace_id": str(workspace.id)},
        )
        session.add(conversation)
        await session.flush()
        if grant:
            await AgentGrantService(session).grant(
                workspace_id=workspace.id,
                agent_id=agent.id,
                actor=actor,
            )
        run_id = None
        if run_workspace_id is not None:
            run = AgentRun(
                conversation_id=conversation.id,
                agent_id=agent.id,
                triggered_by_id=actor.id,
                status=AgentRunStatus.QUEUED,
                context_snapshot={"workspace_id": str(run_workspace_id)},
                metadata_={},
            )
            session.add(run)
            await session.flush()
            run_id = run.id
        now = datetime.now(UTC)
        voice_session = VoiceSessionRecord(
            user_id=actor.id,
            conversation_id=conversation.id,
            run_id=run_id,
            correlation_id=uuid4(),
            state=VoiceState.IDLE.value,
            language="en",
            locale="en-GB",
            timezone="Europe/London",
            created_at=now,
            updated_at=now,
        )
        session.add(voice_session)
        await session.commit()
        return voice_session.id


async def _count_rows(context: AuthTestContext, model: Any) -> int:
    async with context.session_factory() as session:
        return int(await session.scalar(select(func.count()).select_from(model)))


def _founder_headers(context: AuthTestContext) -> dict[str, str]:
    tokens = login_user(context, "founder@example.com")
    return bearer(tokens["access_token"])


def _endpoint(session_id: UUID) -> str:
    return (
        "/api/v1/admin/founder/voice/sessions/"
        f"{session_id}/authorization-diagnostic"
    )


def test_founder_can_inspect_authorized_session_without_business_mutation(
    management_context: AuthTestContext,
    founder_allowlist: None,
) -> None:
    register_user(management_context, "target@example.com")
    register_user(management_context, "founder@example.com")
    grant_role(management_context, "founder@example.com", "administrator")
    session_id = asyncio.run(
        _create_voice_session(
            management_context,
            "target@example.com",
            grant=True,
        )
    )
    before = {
        "voice": asyncio.run(
            _count_rows(management_context, VoiceSessionRecord)
        ),
        "conversations": asyncio.run(
            _count_rows(management_context, AgentConversation)
        ),
        "runs": asyncio.run(_count_rows(management_context, AgentRun)),
        "audit": asyncio.run(_count_rows(management_context, AuditLog)),
    }

    response = management_context.client.get(
        _endpoint(session_id),
        headers=_founder_headers(management_context),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["authorized"] is True
    assert body["first_failing_gate"] is None
    assert body["actor_active"] is True
    assert body["actor_verified"] is True
    assert body["actor_roles"] == ["manager"]
    assert body["gates"]["workspace_agent_grant"] == "pass"
    assert body["gates"]["can_invoke_agents"] == "pass"
    assert body["gates"]["run_binding"] == "not_evaluated"
    assert not {"email", "password", "token", "secret"}.intersection(
        response.text.lower()
    )

    after = {
        "voice": asyncio.run(
            _count_rows(management_context, VoiceSessionRecord)
        ),
        "conversations": asyncio.run(
            _count_rows(management_context, AgentConversation)
        ),
        "runs": asyncio.run(_count_rows(management_context, AgentRun)),
        "audit": asyncio.run(_count_rows(management_context, AuditLog)),
    }
    assert after["voice"] == before["voice"]
    assert after["conversations"] == before["conversations"]
    assert after["runs"] == before["runs"]
    assert after["audit"] == before["audit"] + 1
    async def read_audit() -> tuple[AuditAction, AuditEntity]:
        async with management_context.session_factory() as session:
            row = await session.scalar(
                select(AuditLog)
                .where(
                    AuditLog.entity == AuditEntity.VOICE_AUTHORIZATION_DIAGNOSTIC
                )
                .order_by(AuditLog.timestamp.desc())
            )
            assert row is not None
            return row.action, row.entity

    assert asyncio.run(read_audit()) == (
        AuditAction.READ,
        AuditEntity.VOICE_AUTHORIZATION_DIAGNOSTIC,
    )


def test_non_founder_cannot_inspect_another_users_session(
    management_context: AuthTestContext,
    founder_allowlist: None,
) -> None:
    register_user(management_context, "target@example.com")
    register_user(management_context, "other@example.com")
    session_id = asyncio.run(
        _create_voice_session(management_context, "target@example.com")
    )
    response = management_context.client.get(
        _endpoint(session_id),
        headers=bearer(
            login_user(management_context, "other@example.com")[
                "access_token"
            ]
        ),
    )
    assert response.status_code == 403


def test_viewer_cannot_use_diagnostic_as_privilege_escalation(
    management_context: AuthTestContext,
    founder_allowlist: None,
) -> None:
    register_user(management_context, "viewer@example.com")
    grant_role(management_context, "viewer@example.com", "viewer")
    session_id = asyncio.run(
        _create_voice_session(management_context, "viewer@example.com")
    )
    response = management_context.client.get(
        _endpoint(session_id),
        headers=bearer(
            login_user(management_context, "viewer@example.com")[
                "access_token"
            ]
        ),
    )
    assert response.status_code == 403


def test_missing_workspace_agent_grant_is_reported(
    management_context: AuthTestContext,
    founder_allowlist: None,
) -> None:
    register_user(management_context, "target@example.com")
    register_user(management_context, "founder@example.com")
    grant_role(management_context, "founder@example.com", "administrator")
    session_id = asyncio.run(
        _create_voice_session(management_context, "target@example.com")
    )
    response = management_context.client.get(
        _endpoint(session_id),
        headers=_founder_headers(management_context),
    )
    body = response.json()
    assert response.status_code == 200
    assert body["authorized"] is False
    assert body["first_failing_gate"] == "workspace_agent_grant"
    assert body["gates"]["workspace_agent_grant"] == "fail"
    assert body["gates"]["can_invoke_agents"] == "pass"


def test_non_invoking_role_is_reported_after_grant(
    management_context: AuthTestContext,
    founder_allowlist: None,
) -> None:
    register_user(management_context, "target@example.com")
    register_user(management_context, "founder@example.com")
    grant_role(management_context, "target@example.com", "viewer")
    grant_role(management_context, "founder@example.com", "administrator")
    session_id = asyncio.run(
        _create_voice_session(
            management_context,
            "target@example.com",
            grant=True,
        )
    )
    response = management_context.client.get(
        _endpoint(session_id),
        headers=_founder_headers(management_context),
    )
    body = response.json()
    assert response.status_code == 200
    assert body["authorized"] is False
    assert body["first_failing_gate"] == "can_invoke_agents"
    assert body["actor_roles"] == ["viewer"]
    assert body["gates"]["workspace_agent_grant"] == "pass"
    assert body["gates"]["can_invoke_agents"] == "fail"


def test_run_workspace_mismatch_is_reported_without_run_mutation(
    management_context: AuthTestContext,
    founder_allowlist: None,
) -> None:
    register_user(management_context, "target@example.com")
    register_user(management_context, "founder@example.com")
    grant_role(management_context, "founder@example.com", "administrator")
    session_id = asyncio.run(
        _create_voice_session(
            management_context,
            "target@example.com",
            grant=True,
            run_workspace_id=uuid4(),
        )
    )
    before_runs = asyncio.run(
        _count_rows(management_context, AgentRun)
    )
    response = management_context.client.get(
        _endpoint(session_id),
        headers=_founder_headers(management_context),
    )
    body = response.json()
    assert response.status_code == 200
    assert body["authorized"] is False
    assert body["first_failing_gate"] == "workspace_affinity"
    assert body["gates"]["workspace_affinity"] == "fail"
    assert body["gates"]["run_workspace_affinity"] == "fail"
    assert body["gates"]["run_binding"] == "not_evaluated"
    assert asyncio.run(
        _count_rows(management_context, AgentRun)
    ) == before_runs
