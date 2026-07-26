import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select

from app.database.models import (
    AuditEntity,
    AuditLog,
    EmailDraft,
    Notification,
    NotificationType,
    OutreachApproval,
)
from tests.auth.conftest import AuthTestContext
from tests.auth.helpers import bearer, grant_role, login_user, register_user

UTC = timezone.utc


def user_with_role(
    context: AuthTestContext, email: str, role: str
) -> tuple[dict[str, object], dict[str, str]]:
    user = register_user(context, email)
    grant_role(context, email, role)
    token = login_user(context, email)["access_token"]
    return user, bearer(token)


def test_outreach_template_draft_visibility_and_analytics(
    management_context: AuthTestContext,
) -> None:
    manager, headers = user_with_role(
        management_context, "outreach-manager@example.com", "manager"
    )
    _, other_headers = user_with_role(
        management_context, "other-outreach@example.com", "manager"
    )
    mailbox = management_context.client.post(
        "/api/v1/outreach/mailboxes",
        headers=headers,
        json={
            "provider": "gmail",
            "email_address": "Sales@Example.com",
            "credential_reference": "vault://mailboxes/sales",
            "daily_send_limit": 20,
        },
    )
    assert mailbox.status_code == 201
    assert mailbox.json()["email_address"] == "sales@example.com"
    assert "credential_reference" not in mailbox.json()

    template = management_context.client.post(
        "/api/v1/outreach/templates",
        headers=headers,
        json={
            "name": "Introduction",
            "subject": "Hello {{first_name}}",
            "body_html": "<p>Hello {{first_name}}</p>",
            "variables": ["first_name"],
        },
    )
    assert template.status_code == 201
    version_id = template.json()["versions"][0]["id"]
    draft = management_context.client.post(
        "/api/v1/outreach/drafts",
        headers=headers,
        json={
            "mailbox_id": mailbox.json()["id"],
            "template_version_id": version_id,
            "to_email": "prospect@example.com",
            "subject": "unused",
            "body_html": "unused",
            "variable_values": {"first_name": "<Ada>"},
        },
    )
    assert draft.status_code == 201
    assert draft.json()["subject"] == "Hello <Ada>"
    assert draft.json()["body_html"] == "<p>Hello &lt;Ada&gt;</p>"
    assert draft.json()["owner_id"] == manager["id"]

    hidden = management_context.client.get(
        "/api/v1/outreach/drafts", headers=other_headers
    )
    assert hidden.status_code == 200
    assert hidden.json()["total"] == 0
    analytics = management_context.client.get(
        "/api/v1/analytics/outreach", headers=headers
    )
    assert analytics.status_code == 200
    assert analytics.json()["drafts_by_status"]["draft"] == 1
    assert analytics.json()["delivery_rate"] == 0


def test_approval_workflow_is_reviewer_scoped_and_audited(
    management_context: AuthTestContext,
) -> None:
    _, manager_headers = user_with_role(
        management_context, "approval-manager@example.com", "manager"
    )
    reviewer, reviewer_headers = user_with_role(
        management_context, "approval-executive@example.com", "executive"
    )
    _, other_headers = user_with_role(
        management_context, "approval-other@example.com", "manager"
    )
    mailbox = management_context.client.post(
        "/api/v1/outreach/mailboxes",
        headers=manager_headers,
        json={
            "provider": "smtp",
            "email_address": "owner@example.com",
            "credential_reference": "vault://smtp/owner",
        },
    ).json()
    draft = management_context.client.post(
        "/api/v1/outreach/drafts",
        headers=manager_headers,
        json={
            "mailbox_id": mailbox["id"],
            "to_email": "client@example.com",
            "subject": "Proposal",
            "body_html": "<p>Proposal</p>",
        },
    ).json()
    requested = management_context.client.post(
        f"/api/v1/outreach/drafts/{draft['id']}/approval",
        headers=manager_headers,
        json={"reviewer_id": reviewer["id"]},
    )
    assert requested.status_code == 201
    approval_id = requested.json()["id"]
    denied = management_context.client.post(
        f"/api/v1/outreach/approvals/{approval_id}/decision",
        headers=other_headers,
        json={"approved": True},
    )
    assert denied.status_code == 404
    approved = management_context.client.post(
        f"/api/v1/outreach/approvals/{approval_id}/decision",
        headers=reviewer_headers,
        json={"approved": True},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved"

    async def verify() -> None:
        async with management_context.session_factory() as session:
            approval = await session.get(OutreachApproval, UUID(approval_id))
            draft_row = await session.get(EmailDraft, UUID(draft["id"]))
            notifications = list(
                (
                    await session.scalars(
                        select(Notification).where(
                            Notification.user_id == UUID(str(reviewer["id"])),
                            Notification.type
                            == NotificationType.OUTREACH_APPROVAL_REQUESTED,
                        )
                    )
                ).all()
            )
            audit = await session.scalar(
                select(AuditLog).where(AuditLog.entity == AuditEntity.EMAIL_DRAFT)
            )
            assert approval is not None
            assert draft_row is not None
            assert draft_row.status.value == "approved"
            assert len(notifications) == 1
            assert audit is not None

    asyncio.run(verify())


def test_schedule_validation_and_viewer_mutation_denial(
    management_context: AuthTestContext,
) -> None:
    _, viewer_headers = user_with_role(
        management_context, "outreach-viewer@example.com", "viewer"
    )
    denied = management_context.client.post(
        "/api/v1/outreach/mailboxes",
        headers=viewer_headers,
        json={
            "provider": "gmail",
            "email_address": "viewer@example.com",
            "credential_reference": "vault://viewer",
        },
    )
    assert denied.status_code == 403
    naive = management_context.client.post(
        "/api/v1/outreach/drafts",
        headers=viewer_headers,
        json={
            "mailbox_id": "00000000-0000-0000-0000-000000000000",
            "to_email": "target@example.com",
            "subject": "Test",
            "body_html": "Test",
            "scheduled_at": datetime.now().isoformat(),
        },
    )
    assert naive.status_code == 422

    aware_time = datetime.now(UTC) + timedelta(days=1)
    assert aware_time.utcoffset() is not None
