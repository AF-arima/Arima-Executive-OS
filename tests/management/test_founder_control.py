import asyncio
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.database.models import (
    AgentDefinition,
    AgentStatus,
    AuditAction,
    AuditEntity,
    AuditLog,
    DataFeedObservation,
    User,
    Workspace,
    WorkspaceAgentGrant,
    WorkspaceMembership,
    WithdrawalCircuitBreaker,
    WithdrawalCircuitState,
)
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


def test_founder_customer_inspection_and_circuit_controls_remain_server_authorized(
    management_context: AuthTestContext,
    founder_allowlist: None,
) -> None:
    register_user(management_context, "founder@example.com")
    register_user(management_context, "customer@example.com")
    register_user(management_context, "normal@example.com")
    grant_role(management_context, "founder@example.com", "administrator")

    async def seed_circuit() -> UUID:
        async with management_context.session_factory() as session:
            customer = await session.scalar(select(User).where(User.email == "customer@example.com"))
            assert customer is not None
            workspace = await session.scalar(select(Workspace).where(Workspace.owner_id == customer.id))
            assert workspace is not None
            session.add(WithdrawalCircuitBreaker(workspace_id=workspace.id, state=WithdrawalCircuitState.ENABLED.value))
            await session.commit()
            return workspace.id

    workspace_id = asyncio.run(seed_circuit())
    founder_headers = bearer(login_user(management_context, "founder@example.com")["access_token"])
    normal_headers = bearer(login_user(management_context, "normal@example.com")["access_token"])

    assert management_context.client.get("/api/v1/support/customers?q=customer@example.com", headers=normal_headers).status_code == 403
    search = management_context.client.get("/api/v1/support/customers?q=customer@example.com", headers=founder_headers)
    assert search.status_code == 200
    assert search.json()[0]["email"] == "customer@example.com"
    customer_id = search.json()[0]["id"]
    detail = management_context.client.get(f"/api/v1/support/customers/{customer_id}", headers=founder_headers)
    assert detail.status_code == 200
    assert detail.json()["email"] == "customer@example.com"
    assert detail.json()["workspace_id"] == str(workspace_id)

    circuit_path = f"/api/v1/withdrawals/operations/circuit/{workspace_id}"
    assert management_context.client.get(circuit_path, headers=normal_headers).status_code == 403
    assert management_context.client.get(circuit_path, headers=founder_headers).json()["state"] == "enabled"
    missing_csrf = management_context.client.post(circuit_path, headers=founder_headers, json={"state": "paused", "reason": "test"})
    assert missing_csrf.status_code == 403
    changed = management_context.client.post(circuit_path, headers={**founder_headers, **csrf_headers(management_context)}, json={"state": "paused", "reason": "maintenance"})
    assert changed.status_code == 200, changed.text
    assert changed.json()["state"] == "paused"
    assert management_context.client.post(circuit_path, headers={**normal_headers, **csrf_headers(management_context)}, json={"state": "enabled", "reason": "forbidden"}).status_code == 403
    assert management_context.client.get(circuit_path, headers=founder_headers).json()["state"] == "paused"


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


async def _create_agent_and_workspace_ids(
    context: AuthTestContext,
    *,
    actor_email: str,
    status: AgentStatus = AgentStatus.ACTIVE,
) -> tuple[UUID, UUID]:
    async with context.session_factory() as session:
        actor = await session.scalar(select(User).where(User.email == actor_email))
        assert actor is not None
        workspace = await session.scalar(
            select(Workspace).where(Workspace.owner_id == actor.id)
        )
        assert workspace is not None
        agent = AgentDefinition(
            slug=f"founder-grant-{uuid4()}",
            name="Founder Grant Test Agent",
            description=None,
            system_instructions="Test agent",
            status=status,
            version=1,
            is_default=False,
            created_by_id=actor.id,
        )
        session.add(agent)
        await session.commit()
        return workspace.id, agent.id


def _founder_grant_endpoint(workspace_id: UUID, agent_id: UUID) -> str:
    return (
        "/api/v1/admin/founder/workspaces/"
        f"{workspace_id}/agents/{agent_id}/grant"
    )


def test_founder_can_grant_active_agent_and_audit_it(
    management_context: AuthTestContext,
    founder_allowlist: None,
) -> None:
    register_user(management_context, "founder@example.com")
    grant_role(management_context, "founder@example.com", "administrator")
    workspace_id, agent_id = asyncio.run(
        _create_agent_and_workspace_ids(
            management_context,
            actor_email="founder@example.com",
        )
    )
    headers = {
        **bearer(login_user(management_context, "founder@example.com")["access_token"]),
        **csrf_headers(management_context),
    }

    response = management_context.client.post(
        _founder_grant_endpoint(workspace_id, agent_id),
        headers=headers,
    )

    assert response.status_code == 200
    assert set(response.json()) == {"workspace_id", "agent_id", "status"}
    assert response.json()["workspace_id"] == str(workspace_id)
    assert response.json()["agent_id"] == str(agent_id)
    assert response.json()["status"] == "active"
    assert not {"granted_by", "revoked_at", "secret", "token"}.intersection(
        response.json()
    )

    async def audit_row() -> AuditLog | None:
        async with management_context.session_factory() as session:
            return await session.scalar(
                select(AuditLog)
                .where(
                    AuditLog.entity == AuditEntity.AUTOMATION,
                    AuditLog.action == AuditAction.ASSIGNMENT,
                    AuditLog.entity_id.in_(
                        select(WorkspaceAgentGrant.id).where(
                            WorkspaceAgentGrant.workspace_id == workspace_id,
                            WorkspaceAgentGrant.agent_id == agent_id,
                        )
                    ),
                )
                .order_by(AuditLog.timestamp.desc())
            )

    assert asyncio.run(audit_row()) is not None


def test_founder_grant_restores_revoked_grant(
    management_context: AuthTestContext,
    founder_allowlist: None,
) -> None:
    register_user(management_context, "founder@example.com")
    grant_role(management_context, "founder@example.com", "administrator")
    workspace_id, agent_id = asyncio.run(
        _create_agent_and_workspace_ids(
            management_context,
            actor_email="founder@example.com",
        )
    )
    headers = {
        **bearer(login_user(management_context, "founder@example.com")["access_token"]),
        **csrf_headers(management_context),
    }
    endpoint = _founder_grant_endpoint(workspace_id, agent_id)
    assert management_context.client.post(endpoint, headers=headers).status_code == 200

    async def revoke_for_setup() -> None:
        async with management_context.session_factory() as session:
            grant = await session.scalar(
                select(WorkspaceAgentGrant).where(
                    WorkspaceAgentGrant.workspace_id == workspace_id,
                    WorkspaceAgentGrant.agent_id == agent_id,
                )
            )
            assert grant is not None
            grant.revoked_at = datetime.now(UTC)
            await session.commit()

    asyncio.run(revoke_for_setup())
    assert management_context.client.post(endpoint, headers=headers).status_code == 200

    async def assert_restored() -> None:
        async with management_context.session_factory() as session:
            grant = await session.scalar(
                select(WorkspaceAgentGrant).where(
                    WorkspaceAgentGrant.workspace_id == workspace_id,
                    WorkspaceAgentGrant.agent_id == agent_id,
                )
            )
            assert grant is not None
            assert grant.revoked_at is None

    asyncio.run(assert_restored())


def test_founder_grant_rejects_non_owner_and_cross_workspace(
    management_context: AuthTestContext,
    founder_allowlist: None,
) -> None:
    register_user(management_context, "founder@example.com")
    register_user(management_context, "workspace-owner@example.com")
    grant_role(management_context, "founder@example.com", "administrator")
    owner_workspace_id, agent_id = asyncio.run(
        _create_agent_and_workspace_ids(
            management_context,
            actor_email="workspace-owner@example.com",
        )
    )
    founder_workspace_id, _ = asyncio.run(
        _create_agent_and_workspace_ids(
            management_context,
            actor_email="founder@example.com",
        )
    )
    founder_headers = {
        **bearer(login_user(management_context, "founder@example.com")["access_token"]),
        **csrf_headers(management_context),
    }

    assert (
        management_context.client.post(
            _founder_grant_endpoint(owner_workspace_id, agent_id),
            headers=founder_headers,
        ).status_code
        == 403
    )

    async def add_non_owner_membership() -> None:
        async with management_context.session_factory() as session:
            founder = await session.scalar(
                select(User).where(User.email == "founder@example.com")
            )
            assert founder is not None
            session.add(
                WorkspaceMembership(
                    workspace_id=owner_workspace_id,
                    user_id=founder.id,
                    role="member",
                )
            )
            await session.commit()

    asyncio.run(add_non_owner_membership())
    assert (
        management_context.client.post(
            _founder_grant_endpoint(owner_workspace_id, agent_id),
            headers=founder_headers,
        ).status_code
        == 403
    )
    assert founder_workspace_id != owner_workspace_id


def test_founder_grant_rejects_inactive_agent_non_founder_and_missing_csrf(
    management_context: AuthTestContext,
    founder_allowlist: None,
) -> None:
    register_user(management_context, "founder@example.com")
    register_user(management_context, "normal@example.com")
    grant_role(management_context, "founder@example.com", "administrator")
    workspace_id, inactive_agent_id = asyncio.run(
        _create_agent_and_workspace_ids(
            management_context,
            actor_email="founder@example.com",
            status=AgentStatus.DISABLED,
        )
    )
    endpoint = _founder_grant_endpoint(workspace_id, inactive_agent_id)
    founder_token = login_user(management_context, "founder@example.com")[
        "access_token"
    ]
    assert (
        management_context.client.post(
            endpoint,
            headers=bearer(founder_token),
        ).status_code
        == 403
    )
    assert (
        management_context.client.post(
            endpoint,
            headers={**bearer(founder_token), **csrf_headers(management_context)},
        ).status_code
        == 403
    )
    normal_token = login_user(management_context, "normal@example.com")[
        "access_token"
    ]
    assert (
        management_context.client.post(
            endpoint,
            headers={**bearer(normal_token), **csrf_headers(management_context)},
        ).status_code
        == 403
    )


def test_founder_voice_grant_target_is_founder_only_and_redacted(
    management_context: AuthTestContext,
    founder_allowlist: None,
) -> None:
    register_user(management_context, "founder@example.com")
    register_user(management_context, "normal@example.com")
    grant_role(management_context, "founder@example.com", "administrator")
    workspace_id, _ = asyncio.run(
        _create_agent_and_workspace_ids(
            management_context,
            actor_email="founder@example.com",
        )
    )

    async def create_default_agent() -> UUID:
        async with management_context.session_factory() as session:
            founder = await session.scalar(
                select(User).where(User.email == "founder@example.com")
            )
            assert founder is not None
            agent = AgentDefinition(
                slug=f"founder-default-{uuid4()}",
                name="Founder Default Agent",
                description="not returned",
                system_instructions="not returned",
                status=AgentStatus.ACTIVE,
                version=1,
                is_default=True,
                created_by_id=founder.id,
            )
            session.add(agent)
            await session.commit()
            return agent.id

    agent_id = asyncio.run(create_default_agent())
    founder_headers = bearer(
        login_user(management_context, "founder@example.com")["access_token"]
    )
    normal_headers = bearer(
        login_user(management_context, "normal@example.com")["access_token"]
    )

    assert (
        management_context.client.get(
            "/api/v1/admin/founder/voice/grant-target",
            headers=normal_headers,
        ).status_code
        == 403
    )
    response = management_context.client.get(
        "/api/v1/admin/founder/voice/grant-target",
        headers=founder_headers,
    )

    assert response.status_code == 200
    assert response.json() == {
        "workspace_id": str(workspace_id),
        "agent_id": str(agent_id),
        "agent_name": "Founder Default Agent",
        "agent_status": "active",
    }
    assert not {"description", "system_instructions", "email", "token", "secret"}.intersection(
        response.json()
    )
