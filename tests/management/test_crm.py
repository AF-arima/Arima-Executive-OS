import asyncio
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, select

from app.database.models import (
    AuditEntity,
    AuditLog,
    CRMActivity,
    Deal,
    Lead,
    Notification,
)
from app.services.notification import NotificationService
from tests.auth.conftest import AuthTestContext
from tests.auth.helpers import bearer, grant_role, login_user, register_user


def user_with_role(
    context: AuthTestContext, email: str, role: str
) -> tuple[dict[str, object], dict[str, str]]:
    user = register_user(context, email)
    grant_role(context, email, role)
    token = login_user(context, email)["access_token"]
    return user, bearer(token)


def test_company_contact_crud_normalization_and_visibility(
    management_context: AuthTestContext,
) -> None:
    manager, headers = user_with_role(
        management_context, "crm-manager@example.com", "manager"
    )
    _, other_headers = user_with_role(
        management_context, "other-crm-manager@example.com", "manager"
    )
    company_response = management_context.client.post(
        "/api/v1/crm/companies",
        headers=headers,
        json={
            "name": "  Arima Capital  ",
            "domain": "HTTPS://WWW.Example.COM/path",
            "industry": "Fintech",
        },
    )
    assert company_response.status_code == 201
    company = company_response.json()
    assert company["name"] == "Arima Capital"
    assert company["domain"] == "www.example.com"
    assert company["owner_id"] == manager["id"]

    duplicate = management_context.client.post(
        "/api/v1/crm/companies",
        headers=headers,
        json={"name": "Duplicate", "domain": "www.example.com"},
    )
    assert duplicate.status_code == 409

    contact_response = management_context.client.post(
        "/api/v1/crm/contacts",
        headers=headers,
        json={
            "company_id": company["id"],
            "first_name": " Ada ",
            "last_name": " Lovelace ",
            "email": "ADA@EXAMPLE.COM",
        },
    )
    assert contact_response.status_code == 201
    assert contact_response.json()["email"] == "ada@example.com"

    search = management_context.client.get(
        "/api/v1/crm/companies",
        headers=headers,
        params={"search": "ARIMA", "sort_by": "name"},
    )
    assert search.status_code == 200
    assert search.json()["total"] == 1

    hidden = management_context.client.get(
        f"/api/v1/crm/companies/{company['id']}",
        headers=other_headers,
    )
    assert hidden.status_code == 404
    hidden_list = management_context.client.get(
        "/api/v1/crm/companies", headers=other_headers
    )
    assert hidden_list.json()["total"] == 0
    own_activity = management_context.client.get(
        "/api/v1/activity",
        headers=headers,
        params={"entity": "company"},
    )
    assert own_activity.status_code == 200
    assert own_activity.json()["total"] == 1
    hidden_activity = management_context.client.get(
        "/api/v1/activity",
        headers=other_headers,
        params={"entity": "company"},
    )
    assert hidden_activity.status_code == 200
    assert hidden_activity.json()["total"] == 0


def test_lead_transitions_pipeline_deal_and_idempotent_conversion(
    management_context: AuthTestContext,
) -> None:
    executive, headers = user_with_role(
        management_context, "crm-executive@example.com", "executive"
    )
    pipeline = management_context.client.post(
        "/api/v1/crm/pipelines",
        headers=headers,
        json={"name": "Enterprise", "is_default": True},
    ).json()
    stages = []
    for payload in (
        {
            "name": "Discovery",
            "position": 0,
            "probability": 25,
        },
        {
            "name": "Won",
            "position": 1,
            "probability": 100,
            "is_closed": True,
            "is_won": True,
        },
        {
            "name": "Lost",
            "position": 2,
            "probability": 0,
            "is_closed": True,
        },
    ):
        response = management_context.client.post(
            f"/api/v1/crm/pipelines/{pipeline['id']}/stages",
            headers=headers,
            json=payload,
        )
        assert response.status_code == 201
        stages.append(response.json())
    reordered = management_context.client.post(
        f"/api/v1/crm/pipelines/{pipeline['id']}/stages/reorder",
        headers=headers,
        json={
            "stages": [
                {"stage_id": stage["id"], "position": 2 - index}
                for index, stage in enumerate(stages)
            ]
        },
    )
    assert reordered.status_code == 200
    assert [item["position"] for item in reordered.json()] == [0, 1, 2]

    lead_response = management_context.client.post(
        "/api/v1/crm/leads",
        headers=headers,
        json={
            "title": "Executive OS opportunity",
            "source": "referral",
            "score": 90,
            "estimated_value": "125000.00",
        },
    )
    assert lead_response.status_code == 201
    lead = lead_response.json()
    invalid = management_context.client.post(
        f"/api/v1/crm/leads/{lead['id']}/convert",
        headers=headers,
        json={},
    )
    assert invalid.status_code == 409
    qualified = management_context.client.patch(
        f"/api/v1/crm/leads/{lead['id']}",
        headers=headers,
        json={"status": "qualified"},
    )
    assert qualified.status_code == 200
    assert qualified.json()["qualified_at"] is not None

    converted = management_context.client.post(
        f"/api/v1/crm/leads/{lead['id']}/convert",
        headers=headers,
        json={
            "pipeline_id": pipeline["id"],
            "stage_id": stages[0]["id"],
        },
    )
    assert converted.status_code == 200
    deal = converted.json()
    assert deal["originating_lead_id"] == lead["id"]
    assert deal["owner_id"] == executive["id"]
    repeated = management_context.client.post(
        f"/api/v1/crm/leads/{lead['id']}/convert",
        headers=headers,
        json={},
    )
    assert repeated.status_code == 200
    assert repeated.json()["id"] == deal["id"]

    won = management_context.client.patch(
        f"/api/v1/crm/deals/{deal['id']}/stage",
        headers=headers,
        json={"stage_id": stages[1]["id"]},
    )
    assert won.status_code == 200
    assert won.json()["status"] == "won"
    assert won.json()["actual_close_date"] is not None
    reopened = management_context.client.patch(
        f"/api/v1/crm/deals/{deal['id']}/stage",
        headers=headers,
        json={"stage_id": stages[0]["id"]},
    )
    assert reopened.status_code == 200
    assert reopened.json()["status"] == "open"
    assert reopened.json()["actual_close_date"] is None

    rollback_lead = management_context.client.post(
        "/api/v1/crm/leads",
        headers=headers,
        json={
            "title": "Rollback conversion",
            "source": "partner",
            "status": "qualified",
            "owner_id": executive["id"],
        },
    ).json()

    converted_rollback_lead = management_context.client.post(
        f"/api/v1/crm/leads/{rollback_lead['id']}/convert",
        headers=headers,
        json={
            "pipeline_id": pipeline["id"],
            "stage_id": stages[0]["id"],
        },
    )
    assert converted_rollback_lead.status_code == 200

    async def rolled_back() -> tuple[str, int]:
        async with management_context.session_factory() as session:
            stored = await session.get(Lead, UUID(rollback_lead["id"]))
            assert stored is not None
            count = await session.scalar(
                select(func.count(Deal.id)).where(
                    Deal.originating_lead_id == UUID(rollback_lead["id"])
                )
            )
            return stored.status.value, int(count or 0)

    assert asyncio.run(rolled_back()) == ("converted", 1)

    async def counts() -> tuple[int, int, int]:
        async with management_context.session_factory() as session:
            deal_count = await session.scalar(
                select(func.count(Deal.id)).where(
                    Deal.originating_lead_id == UUID(lead["id"])
                )
            )
            converted_count = await session.scalar(
                select(func.count(Lead.id)).where(
                    Lead.id == UUID(lead["id"]),
                    Lead.converted_at.is_not(None),
                )
            )
            audit_count = await session.scalar(
                select(func.count(AuditLog.id)).where(
                    AuditLog.entity == AuditEntity.LEAD
                )
            )
            return (
                int(deal_count or 0),
                int(converted_count or 0),
                int(audit_count or 0),
            )

    assert asyncio.run(counts()) == (1, 1, 6)


def test_notes_activities_notifications_and_analytics(
    management_context: AuthTestContext,
) -> None:
    manager, headers = user_with_role(
        management_context, "activity-manager@example.com", "manager"
    )
    _, analyst_headers = user_with_role(
        management_context, "assigned-analyst@example.com", "analyst"
    )
    lead = management_context.client.post(
        "/api/v1/crm/leads",
        headers=headers,
        json={
            "title": "Follow-up lead",
            "source": "website",
            "owner_id": manager["id"],
        },
    ).json()
    invalid_note = management_context.client.post(
        "/api/v1/crm/notes",
        headers=headers,
        json={"body": "Private", "lead_id": lead["id"], "deal_id": lead["id"]},
    )
    assert invalid_note.status_code == 422
    note = management_context.client.post(
        "/api/v1/crm/notes",
        headers=headers,
        json={"body": "Discuss requirements", "lead_id": lead["id"]},
    )
    assert note.status_code == 201

    activity = management_context.client.post(
        "/api/v1/crm/activities",
        headers=headers,
        json={
            "type": "follow_up",
            "subject": "Call prospect",
            "lead_id": lead["id"],
            "assigned_to": manager["id"],
            "due_at": "2026-07-24T12:00:00Z",
        },
    )
    assert activity.status_code == 201
    activity_id = activity.json()["id"]

    async def generate_due() -> tuple[int, int]:
        async with management_context.session_factory() as session:
            first = await NotificationService(
                session
            ).create_crm_due_notifications(
                now=datetime(2026, 7, 23, tzinfo=timezone.utc)
            )
        async with management_context.session_factory() as session:
            second = await NotificationService(
                session
            ).create_crm_due_notifications(
                now=datetime(2026, 7, 23, tzinfo=timezone.utc)
            )
        return first, second

    assert asyncio.run(generate_due()) == (1, 0)
    completed = management_context.client.post(
        f"/api/v1/crm/activities/{activity_id}/complete",
        headers=analyst_headers,
        json={"outcome": "Interested"},
    )
    assert completed.status_code == 404
    completed = management_context.client.post(
        f"/api/v1/crm/activities/{activity_id}/complete",
        headers=headers,
        json={"outcome": "Interested"},
    )
    assert completed.status_code == 200
    repeated = management_context.client.post(
        f"/api/v1/crm/activities/{activity_id}/complete",
        headers=headers,
        json={"outcome": "Must not overwrite"},
    )
    assert repeated.status_code == 200
    assert repeated.json()["outcome"] == "Interested"

    analytics = management_context.client.get(
        "/api/v1/analytics/crm/activities", headers=headers
    )
    assert analytics.status_code == 200
    assert analytics.json()["completed"] == 1
    lead_analytics = management_context.client.get(
        "/api/v1/analytics/crm/leads", headers=headers
    )
    assert lead_analytics.status_code == 200
    assert lead_analytics.json()["leads_by_source"]["website"] == 1

    async def state() -> tuple[int, int]:
        async with management_context.session_factory() as session:
            notifications = await session.scalar(
                select(func.count(Notification.id)).where(
                    Notification.user_id == UUID(str(manager["id"]))
                )
            )
            completions = await session.scalar(
                select(func.count(CRMActivity.id)).where(
                    CRMActivity.id == UUID(activity_id),
                    CRMActivity.completed_at.is_not(None),
                )
            )
            return int(notifications or 0), int(completions or 0)

    assert asyncio.run(state()) == (1, 1)


def test_viewer_cannot_mutate_crm(
    management_context: AuthTestContext,
) -> None:
    _, headers = user_with_role(
        management_context, "crm-viewer@example.com", "viewer"
    )
    response = management_context.client.post(
        "/api/v1/crm/companies",
        headers=headers,
        json={"name": "Forbidden"},
    )
    assert response.status_code == 403
    _, analyst_headers = user_with_role(
        management_context, "crm-limited-analyst@example.com", "analyst"
    )
    analyst_create = management_context.client.post(
        "/api/v1/crm/companies",
        headers=analyst_headers,
        json={"name": "Analyst cannot create primary CRM records"},
    )
    assert analyst_create.status_code == 403


def test_crm_analytics_cache_is_scoped_and_invalidated(
    management_context: AuthTestContext,
) -> None:
    _, first_headers = user_with_role(
        management_context, "cache-manager-one@example.com", "manager"
    )
    _, second_headers = user_with_role(
        management_context, "cache-manager-two@example.com", "manager"
    )
    for headers, title in (
        (first_headers, "First visible lead"),
        (second_headers, "Second visible lead"),
    ):
        response = management_context.client.post(
            "/api/v1/crm/leads",
            headers=headers,
            json={"title": title, "source": "outbound"},
        )
        assert response.status_code == 201

    first = management_context.client.get(
        "/api/v1/analytics/crm/leads", headers=first_headers
    ).json()
    second = management_context.client.get(
        "/api/v1/analytics/crm/leads", headers=second_headers
    ).json()
    assert sum(first["leads_by_status"].values()) == 1
    assert sum(second["leads_by_status"].values()) == 1
    cached = management_context.client.get(
        "/api/v1/analytics/crm/leads", headers=first_headers
    ).json()
    assert cached["generated_at"] == first["generated_at"]

    created = management_context.client.post(
        "/api/v1/crm/leads",
        headers=first_headers,
        json={"title": "Cache invalidator", "source": "email"},
    )
    assert created.status_code == 201
    refreshed = management_context.client.get(
        "/api/v1/analytics/crm/leads", headers=first_headers
    ).json()
    assert sum(refreshed["leads_by_status"].values()) == 2
