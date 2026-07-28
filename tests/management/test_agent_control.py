import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, select

from app.database.models import AgentApproval, AuditLog, Notification
from tests.auth.conftest import AuthTestContext
from tests.auth.helpers import (
    bearer,
    grant_role,
    login_user,
    register_user,
)

UTC = timezone.utc


def prepare_user(
    context: AuthTestContext,
    email: str,
    role: str,
) -> tuple[dict[str, object], dict[str, str]]:
    user = register_user(context, email)
    grant_role(context, email, role)
    token = login_user(context, email)["access_token"]
    return user, bearer(token)


def create_active_agent(
    context: AuthTestContext,
    headers: dict[str, str],
    *,
    slug: str = "stage-two-agent",
) -> dict[str, object]:
    created = context.client.post(
        "/api/v1/agents",
        headers=headers,
        json={
            "slug": slug,
            "name": slug.replace("-", " ").title(),
            "system_instructions": "Assist the executive.",
        },
    )
    assert created.status_code == 201, created.text
    agent = created.json()
    activated = context.client.patch(
        f"/api/v1/agents/{agent['id']}/activate",
        headers=headers,
    )
    assert activated.status_code == 200, activated.text
    return activated.json()


def create_conversation(
    context: AuthTestContext,
    headers: dict[str, str],
    agent_id: object,
    *,
    title: str = "Executive planning",
) -> dict[str, object]:
    response = context.client.post(
        "/api/v1/conversations",
        headers=headers,
        json={"agent_id": agent_id, "title": title},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_agent_lifecycle_permissions_pagination_audit_and_notification(
    management_context: AuthTestContext,
) -> None:
    admin, admin_headers = prepare_user(
        management_context,
        "agent-admin@example.com",
        "administrator",
    )
    _, viewer_headers = prepare_user(
        management_context,
        "agent-viewer@example.com",
        "viewer",
    )

    denied = management_context.client.post(
        "/api/v1/agents",
        headers=viewer_headers,
        json={
            "slug": "denied-agent",
            "name": "Denied",
            "system_instructions": "No access.",
        },
    )
    assert denied.status_code == 403

    first = create_active_agent(
        management_context,
        admin_headers,
        slug="first-agent",
    )
    made_default = management_context.client.patch(
        f"/api/v1/agents/{first['id']}/default",
        headers=admin_headers,
    )
    assert made_default.status_code == 200
    assert made_default.json()["is_default"] is True
    assert (
        management_context.client.patch(
            f"/api/v1/agents/{first['id']}/archive",
            headers=admin_headers,
        ).status_code
        == 409
    )
    assert (
        management_context.client.patch(
            f"/api/v1/agents/{first['id']}/disable",
            headers=admin_headers,
        ).status_code
        == 409
    )

    second = create_active_agent(
        management_context,
        admin_headers,
        slug="second-agent",
    )
    swapped = management_context.client.patch(
        f"/api/v1/agents/{second['id']}/default",
        headers=admin_headers,
    )
    assert swapped.status_code == 200
    assert swapped.json()["is_default"] is True
    old = management_context.client.get(
        f"/api/v1/agents/{first['id']}",
        headers=viewer_headers,
    )
    assert old.status_code == 200
    assert old.json()["is_default"] is False

    updated = management_context.client.patch(
        f"/api/v1/agents/{first['id']}",
        headers=admin_headers,
        json={"slug": "renamed-agent", "version": 2},
    )
    assert updated.status_code == 200
    assert updated.json()["slug"] == "renamed-agent"
    archived = management_context.client.patch(
        f"/api/v1/agents/{first['id']}/archive",
        headers=admin_headers,
    )
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"
    assert (
        management_context.client.patch(
            f"/api/v1/agents/{first['id']}/activate",
            headers=admin_headers,
        ).status_code
        == 409
    )

    visible = management_context.client.get(
        "/api/v1/agents",
        headers=viewer_headers,
        params={"limit": 1},
    )
    assert visible.status_code == 200
    assert visible.json()["total"] == 1
    all_agents = management_context.client.get(
        "/api/v1/agents",
        headers=viewer_headers,
        params={"include_archived": True, "limit": 1, "offset": 1},
    )
    assert all_agents.status_code == 200
    assert all_agents.json()["total"] == 2
    assert len(all_agents.json()["items"]) == 1
    assert (
        management_context.client.get(
            "/api/v1/agents",
            headers=viewer_headers,
            params={"limit": 0},
        ).status_code
        == 422
    )
    assert (
        management_context.client.post(
            "/api/v1/agents",
            headers=admin_headers,
            json={
                "slug": "Invalid Slug",
                "name": "Invalid",
                "system_instructions": "Invalid.",
            },
        ).status_code
        == 422
    )

    async def counts() -> tuple[int, int]:
        async with management_context.session_factory() as session:
            audits = await session.scalar(
                select(func.count(AuditLog.id)).where(
                    AuditLog.actor_id == UUID(str(admin["id"]))
                )
            )
            notifications = await session.scalar(
                select(func.count(Notification.id)).where(
                    Notification.user_id == UUID(str(admin["id"])),
                    Notification.title == "Agent archived",
                )
            )
            return int(audits or 0), int(notifications or 0)

    audit_count, notification_count = asyncio.run(counts())
    assert audit_count >= 8
    assert notification_count == 1


def test_conversation_ownership_message_ordering_and_closed_writes(
    management_context: AuthTestContext,
) -> None:
    _, admin_headers = prepare_user(
        management_context,
        "conversation-admin@example.com",
        "administrator",
    )
    _, analyst_headers = prepare_user(
        management_context,
        "conversation-owner@example.com",
        "analyst",
    )
    _, other_headers = prepare_user(
        management_context,
        "conversation-other@example.com",
        "analyst",
    )
    _, viewer_headers = prepare_user(
        management_context,
        "conversation-viewer@example.com",
        "viewer",
    )
    agent = create_active_agent(management_context, admin_headers)
    conversation = create_conversation(
        management_context,
        analyst_headers,
        agent["id"],
    )
    conversation_id = conversation["id"]

    assert (
        management_context.client.get(
            f"/api/v1/conversations/{conversation_id}",
            headers=other_headers,
        ).status_code
        == 403
    )
    assert (
        management_context.client.post(
            "/api/v1/conversations",
            headers=viewer_headers,
            json={"agent_id": agent["id"], "title": "Denied"},
        ).status_code
        == 403
    )
    renamed = management_context.client.patch(
        f"/api/v1/conversations/{conversation_id}",
        headers=analyst_headers,
        json={"title": "Renamed planning"},
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "Renamed planning"
    pinned = management_context.client.patch(
        f"/api/v1/conversations/{conversation_id}/pin",
        headers=analyst_headers,
    )
    assert pinned.status_code == 200
    assert pinned.json()["pinned"] is True
    unpinned = management_context.client.patch(
        f"/api/v1/conversations/{conversation_id}/unpin",
        headers=analyst_headers,
    )
    assert unpinned.status_code == 200
    assert unpinned.json()["pinned"] is False

    for content in ("First", "Second", "Third"):
        response = management_context.client.post(
            "/api/v1/messages",
            headers=analyst_headers,
            json={
                "conversation_id": conversation_id,
                "role": "user",
                "content": content,
            },
        )
        assert response.status_code == 201, response.text
    denied_assistant = management_context.client.post(
        "/api/v1/messages",
        headers=analyst_headers,
        json={
            "conversation_id": conversation_id,
            "role": "assistant",
            "content": "Not allowed",
        },
    )
    assert denied_assistant.status_code == 403
    assistant = management_context.client.post(
        "/api/v1/messages",
        headers=admin_headers,
        json={
            "conversation_id": conversation_id,
            "role": "assistant",
            "content": "Administrative response",
        },
    )
    assert assistant.status_code == 403

    messages = management_context.client.get(
        "/api/v1/messages",
        headers=analyst_headers,
        params={"conversation_id": conversation_id, "limit": 2, "offset": 1},
    )
    assert messages.status_code == 200
    assert messages.json()["total"] == 3
    assert [
        item["sequence_number"] for item in messages.json()["items"]
    ] == [2, 3]
    refreshed = management_context.client.get(
        f"/api/v1/conversations/{conversation_id}",
        headers=analyst_headers,
    )
    assert refreshed.json()["last_message_at"] is not None

    closed = management_context.client.patch(
        f"/api/v1/conversations/{conversation_id}/close",
        headers=analyst_headers,
    )
    assert closed.status_code == 200
    assert closed.json()["status"] == "closed"
    assert (
        management_context.client.post(
            "/api/v1/messages",
            headers=analyst_headers,
            json={
                "conversation_id": conversation_id,
                "role": "user",
                "content": "Too late",
            },
        ).status_code
        == 409
    )
    archived = management_context.client.patch(
        f"/api/v1/conversations/{conversation_id}/archive",
        headers=analyst_headers,
    )
    assert archived.status_code == 200
    listed = management_context.client.get(
        "/api/v1/conversations",
        headers=analyst_headers,
    )
    assert listed.json()["total"] == 0
    explicit = management_context.client.get(
        "/api/v1/conversations",
        headers=analyst_headers,
        params={"include_archived": True},
    )
    assert explicit.json()["total"] == 1


def test_run_lifecycle_metrics_notifications_and_illegal_transitions(
    management_context: AuthTestContext,
) -> None:
    owner, owner_headers = prepare_user(
        management_context,
        "run-owner@example.com",
        "analyst",
    )
    _, admin_headers = prepare_user(
        management_context,
        "run-admin@example.com",
        "administrator",
    )
    agent = create_active_agent(management_context, admin_headers)
    conversation = create_conversation(
        management_context,
        owner_headers,
        agent["id"],
    )
    created = management_context.client.post(
        "/api/v1/runs",
        headers=owner_headers,
        json={
            "conversation_id": conversation["id"],
            "prompt_tokens": 10,
            "estimated_cost_gbp": "0.125",
        },
    )
    assert created.status_code == 201
    run_id = created.json()["id"]
    assert created.json()["status"] == "queued"
    assert created.json()["total_tokens"] == 10

    assert (
        management_context.client.patch(
            f"/api/v1/runs/{run_id}",
            headers=owner_headers,
            json={"status": "completed"},
        ).status_code
        == 409
    )
    running = management_context.client.patch(
        f"/api/v1/runs/{run_id}",
        headers=owner_headers,
        json={"status": "running"},
    )
    assert running.status_code == 200
    assert running.json()["started_at"] is not None
    waiting = management_context.client.patch(
        f"/api/v1/runs/{run_id}",
        headers=owner_headers,
        json={"status": "waiting_for_approval"},
    )
    assert waiting.status_code == 200
    resumed = management_context.client.patch(
        f"/api/v1/runs/{run_id}",
        headers=owner_headers,
        json={"status": "running"},
    )
    assert resumed.status_code == 200
    completed = management_context.client.patch(
        f"/api/v1/runs/{run_id}",
        headers=owner_headers,
        json={
            "status": "completed",
            "completion_tokens": 7,
            "estimated_cost_gbp": "0.250000",
        },
    )
    assert completed.status_code == 200, completed.text
    body = completed.json()
    assert body["status"] == "completed"
    assert body["total_tokens"] == 17
    assert body["estimated_cost_gbp"] == "0.250000"
    assert body["latency_ms"] >= 0
    assert body["completed_at"] is not None
    assert (
        management_context.client.patch(
            f"/api/v1/runs/{run_id}",
            headers=owner_headers,
            json={"status": "failed"},
        ).status_code
        == 409
    )
    page = management_context.client.get(
        "/api/v1/runs",
        headers=owner_headers,
        params={"status": "completed", "limit": 1},
    )
    assert page.status_code == 200
    assert page.json()["total"] == 1

    async def completed_notifications() -> int:
        async with management_context.session_factory() as session:
            value = await session.scalar(
                select(func.count(Notification.id)).where(
                    Notification.user_id == UUID(str(owner["id"])),
                    Notification.title == "Agent run completed",
                )
            )
            return int(value or 0)

    assert asyncio.run(completed_notifications()) == 1

    failed_run = management_context.client.post(
        "/api/v1/runs",
        headers=owner_headers,
        json={"conversation_id": conversation["id"]},
    ).json()
    assert (
        management_context.client.patch(
            f"/api/v1/runs/{failed_run['id']}",
            headers=owner_headers,
            json={"status": "running"},
        ).status_code
        == 200
    )
    failed = management_context.client.patch(
        f"/api/v1/runs/{failed_run['id']}",
        headers=owner_headers,
        json={
            "status": "failed",
            "failure_code": "stored_failure",
            "failure_message": "No provider was invoked.",
        },
    )
    assert failed.status_code == 200
    assert failed.json()["failure_code"] == "stored_failure"

    async def failed_notifications() -> int:
        async with management_context.session_factory() as session:
            value = await session.scalar(
                select(func.count(Notification.id)).where(
                    Notification.user_id == UUID(str(owner["id"])),
                    Notification.title == "Agent run failed",
                )
            )
            return int(value or 0)

    assert asyncio.run(failed_notifications()) == 1


def test_approval_and_memory_lifecycles_permissions_and_validation(
    management_context: AuthTestContext,
) -> None:
    owner, owner_headers = prepare_user(
        management_context,
        "approval-owner@example.com",
        "manager",
    )
    _, other_headers = prepare_user(
        management_context,
        "memory-other@example.com",
        "analyst",
    )
    _, manager_headers = prepare_user(
        management_context,
        "approval-manager@example.com",
        "manager",
    )
    _, admin_headers = prepare_user(
        management_context,
        "approval-admin@example.com",
        "administrator",
    )
    _, viewer_headers = prepare_user(
        management_context,
        "memory-viewer@example.com",
        "viewer",
    )
    agent = create_active_agent(management_context, admin_headers)
    conversation = create_conversation(
        management_context,
        owner_headers,
        agent["id"],
    )
    run = management_context.client.post(
        "/api/v1/runs",
        headers=owner_headers,
        json={"conversation_id": conversation["id"]},
    ).json()

    expired_at = (datetime.now(UTC) - timedelta(minutes=1)).isoformat()
    invalid = management_context.client.post(
        "/api/v1/approvals",
        headers=owner_headers,
        json={
            "run_id": run["id"],
            "action_type": "send-email",
            "risk_level": "high",
            "reason": "External side effect",
            "expires_at": expired_at,
        },
    )
    assert invalid.status_code == 409
    requested = management_context.client.post(
        "/api/v1/approvals",
        headers=owner_headers,
        json={
            "run_id": run["id"],
            "action_type": "send-email",
            "risk_level": "high",
            "reason": "External side effect",
            "expires_at": (
                datetime.now(UTC) + timedelta(hours=1)
            ).isoformat(),
        },
    )
    assert requested.status_code == 201, requested.text
    approval_id = requested.json()["id"]
    assert (
        management_context.client.get(
            "/api/v1/approvals/pending",
            headers=owner_headers,
        ).status_code
        == 200
    )
    pending = management_context.client.get(
        "/api/v1/approvals/pending",
        headers=manager_headers,
    )
    assert pending.status_code == 200
    assert pending.json()["total"] == 0
    assert (
        management_context.client.patch(
            f"/api/v1/approvals/{approval_id}",
            headers=manager_headers,
            json={"status": "approved", "decision_note": "Not my approval"},
        ).status_code
        == 404
    )
    approved = management_context.client.patch(
        f"/api/v1/approvals/{approval_id}",
        headers=owner_headers,
        json={"status": "approved", "decision_note": "Approved safely"},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"
    assert approved.json()["decided_at"] is not None
    assert (
        management_context.client.patch(
            f"/api/v1/approvals/{approval_id}",
            headers=owner_headers,
            json={"status": "rejected"},
        ).status_code
        == 409
    )

    def request_approval(action_type: str) -> dict[str, object]:
        response = management_context.client.post(
            "/api/v1/approvals",
            headers=owner_headers,
            json={
                "run_id": run["id"],
                "action_type": action_type,
                "risk_level": "medium",
                "reason": "Lifecycle coverage",
                "expires_at": (
                    datetime.now(UTC) + timedelta(hours=1)
                ).isoformat(),
            },
        )
        assert response.status_code == 201, response.text
        return response.json()

    rejected_candidate = request_approval("reject-action")
    rejected = management_context.client.patch(
        f"/api/v1/approvals/{rejected_candidate['id']}",
        headers=owner_headers,
        json={"status": "rejected"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"

    cancelled_candidate = request_approval("cancel-action")
    cancelled = management_context.client.patch(
        f"/api/v1/approvals/{cancelled_candidate['id']}",
        headers=owner_headers,
        json={"status": "cancelled"},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    expired_candidate = request_approval("expire-action")

    async def make_expired() -> None:
        async with management_context.session_factory() as session:
            approval = await session.get(
                AgentApproval,
                UUID(str(expired_candidate["id"])),
            )
            assert approval is not None
            approval.expires_at = datetime.now(UTC) - timedelta(minutes=1)
            await session.commit()

    asyncio.run(make_expired())
    expired = management_context.client.patch(
        f"/api/v1/approvals/{expired_candidate['id']}",
        headers=owner_headers,
        json={"status": "expired"},
    )
    assert expired.status_code == 200
    assert expired.json()["status"] == "expired"

    memory = management_context.client.post(
        "/api/v1/memory",
        headers=owner_headers,
        json={
            "memory_type": "preference",
            "scope": "user",
            "key": "report.style",
            "value": "Use concise summaries.",
            "importance": 4,
        },
    )
    assert memory.status_code == 201, memory.text
    memory_id = memory.json()["id"]
    assert memory.json()["owner_id"] == owner["id"]
    duplicate = management_context.client.post(
        "/api/v1/memory",
        headers=owner_headers,
        json={
            "memory_type": "preference",
            "scope": "user",
            "key": "report.style",
            "value": "Duplicate.",
        },
    )
    assert duplicate.status_code == 409
    assert (
        management_context.client.get(
            f"/api/v1/memory/{memory_id}",
            headers=other_headers,
        ).status_code
        == 403
    )
    assert (
        management_context.client.post(
            "/api/v1/memory",
            headers=viewer_headers,
            json={
                "memory_type": "fact",
                "scope": "user",
                "key": "denied",
                "value": "No.",
            },
        ).status_code
        == 403
    )
    updated = management_context.client.patch(
        f"/api/v1/memory/{memory_id}",
        headers=owner_headers,
        json={"value": "Use five concise bullets.", "importance": 5},
    )
    assert updated.status_code == 200
    assert updated.json()["importance"] == 5
    searched = management_context.client.get(
        "/api/v1/memory/search",
        headers=owner_headers,
        params={"scope": "user", "key": "report.style"},
    )
    assert searched.status_code == 200
    assert searched.json()["total"] == 1
    disabled = management_context.client.patch(
        f"/api/v1/memory/{memory_id}/disable",
        headers=owner_headers,
    )
    assert disabled.status_code == 200
    assert disabled.json()["is_active"] is False
    assert (
        management_context.client.get(
            f"/api/v1/memory/{memory_id}",
            headers=owner_headers,
        ).status_code
        == 404
    )
    assert (
        management_context.client.delete(
            f"/api/v1/memory/{memory_id}",
            headers=owner_headers,
        ).status_code
        == 204
    )

    async def event_counts() -> tuple[int, int]:
        async with management_context.session_factory() as session:
            notification_count = await session.scalar(
                select(func.count(Notification.id)).where(
                    Notification.user_id == UUID(str(owner["id"])),
                    Notification.entity_type == "agent_approval",
                )
            )
            audit_count = await session.scalar(
                select(func.count(AuditLog.id)).where(
                    AuditLog.actor_id == UUID(str(owner["id"]))
                )
            )
            return int(notification_count or 0), int(audit_count or 0)

    notification_count, audit_count = asyncio.run(event_counts())
    assert notification_count == 6
    assert audit_count >= 9
