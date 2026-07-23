import asyncio
from datetime import datetime, timedelta, timezone

from app.schemas.analytics import DashboardSummary
from app.services.cache import InMemoryDashboardCache
from tests.auth.conftest import AuthTestContext
from tests.management.test_projects_api import prepare_user

UTC = timezone.utc


def test_cache_generation_prevents_stale_repopulation() -> None:
    async def exercise() -> None:
        cache = InMemoryDashboardCache()
        generation = await cache.generation()
        now = datetime.now(UTC)
        summary = DashboardSummary(
            total_projects=0,
            active_projects=0,
            archived_projects=0,
            projects_by_status={},
            total_tasks=0,
            tasks_by_status={},
            tasks_by_priority={},
            completed_tasks=0,
            overdue_tasks=0,
            unassigned_tasks=0,
            completion_rate=0,
            overdue_rate=0,
            average_completion_time_hours=0,
            tasks_due_next_7_days=0,
            tasks_due_next_30_days=0,
            active_users=0,
            recent_activity_count=0,
            generated_at=now,
            range_start=now - timedelta(days=30),
            range_end=now,
        )
        await cache.invalidate()
        stored = await cache.set(
            "stale",
            summary,
            ttl_seconds=60,
            expected_generation=generation,
        )
        assert stored is False
        assert await cache.get("stale") is None

    asyncio.run(exercise())


def create_project(
    context: AuthTestContext,
    headers: dict[str, str],
    name: str,
) -> dict[str, object]:
    response = context.client.post(
        "/api/v1/projects",
        headers=headers,
        json={"name": name, "status": "active"},
    )
    assert response.status_code == 201
    return response.json()


def create_task(
    context: AuthTestContext,
    headers: dict[str, str],
    *,
    project_id: object,
    title: str,
    assigned_to: object | None = None,
    status: str = "in_progress",
    priority: str = "medium",
    due_date: datetime | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "title": title,
        "project_id": project_id,
        "status": status,
        "priority": priority,
    }
    if assigned_to is not None:
        payload["assigned_to"] = assigned_to
    if due_date is not None:
        payload["due_date"] = due_date.isoformat()
    response = context.client.post(
        "/api/v1/tasks",
        headers=headers,
        json=payload,
    )
    assert response.status_code == 201
    return response.json()


def test_dashboard_empty_rates_and_timezone_validation(
    management_context: AuthTestContext,
) -> None:
    _, viewer_headers = prepare_user(
        management_context,
        "empty-viewer@example.com",
        "viewer",
    )
    response = management_context.client.get(
        "/api/v1/dashboard/summary",
        headers=viewer_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total_projects"] == 0
    assert body["total_tasks"] == 0
    assert body["completion_rate"] == 0
    assert body["overdue_rate"] == 0

    naive = management_context.client.get(
        "/api/v1/dashboard/summary",
        headers=viewer_headers,
        params={"start_date": "2026-01-01T00:00:00"},
    )
    assert naive.status_code == 422
    reversed_range = management_context.client.get(
        "/api/v1/dashboard/summary",
        headers=viewer_headers,
        params={
            "start_date": "2026-02-01T00:00:00Z",
            "end_date": "2026-01-01T00:00:00Z",
        },
    )
    assert reversed_range.status_code == 422
    bad_timezone = management_context.client.get(
        "/api/v1/dashboard/summary",
        headers=viewer_headers,
        params={"timezone": "Not/A_Zone"},
    )
    assert bad_timezone.status_code == 422


def test_dashboard_permissions_cache_refresh_and_invalidation(
    management_context: AuthTestContext,
) -> None:
    manager, manager_headers = prepare_user(
        management_context,
        "dashboard-manager@example.com",
        "manager",
    )
    _, other_manager_headers = prepare_user(
        management_context,
        "dashboard-other@example.com",
        "manager",
    )
    analyst, analyst_headers = prepare_user(
        management_context,
        "dashboard-analyst@example.com",
        "analyst",
    )
    _, executive_headers = prepare_user(
        management_context,
        "dashboard-executive@example.com",
        "executive",
    )
    _, viewer_headers = prepare_user(
        management_context,
        "dashboard-viewer@example.com",
        "viewer",
    )
    now = datetime.now(UTC)
    owned = create_project(
        management_context,
        manager_headers,
        "Manager dashboard",
    )
    other = create_project(
        management_context,
        other_manager_headers,
        "Other dashboard",
    )
    create_task(
        management_context,
        manager_headers,
        project_id=owned["id"],
        title="Completed",
        assigned_to=analyst["id"],
        status="completed",
        priority="high",
    )
    create_task(
        management_context,
        manager_headers,
        project_id=owned["id"],
        title="Overdue",
        assigned_to=analyst["id"],
        priority="urgent",
        due_date=now - timedelta(days=1),
    )
    create_task(
        management_context,
        other_manager_headers,
        project_id=other["id"],
        title="Other unassigned",
    )

    manager_summary = management_context.client.get(
        "/api/v1/dashboard/summary",
        headers=manager_headers,
    ).json()
    assert manager_summary["total_projects"] == 1
    assert manager_summary["total_tasks"] == 2
    assert manager_summary["completed_tasks"] == 1
    assert manager_summary["overdue_tasks"] == 1
    assert manager_summary["completion_rate"] == 0.5

    analyst_summary = management_context.client.get(
        "/api/v1/dashboard/summary",
        headers=analyst_headers,
    ).json()
    assert analyst_summary["total_projects"] == 1
    assert analyst_summary["total_tasks"] == 2
    other_summary = management_context.client.get(
        "/api/v1/dashboard/summary",
        headers=other_manager_headers,
    ).json()
    assert other_summary["total_projects"] == 1
    assert other_summary["total_tasks"] == 1
    for headers in (executive_headers, viewer_headers):
        global_summary = management_context.client.get(
            "/api/v1/dashboard/summary",
            headers=headers,
        ).json()
        assert global_summary["total_projects"] == 2
        assert global_summary["total_tasks"] == 3

    cached = management_context.client.get(
        "/api/v1/dashboard/summary",
        headers=manager_headers,
    ).json()
    assert cached["generated_at"] == manager_summary["generated_at"]
    refreshed = management_context.client.get(
        "/api/v1/dashboard/summary",
        headers=manager_headers,
        params={"refresh": "true"},
    ).json()
    assert refreshed["generated_at"] != cached["generated_at"]

    create_task(
        management_context,
        manager_headers,
        project_id=owned["id"],
        title="Invalidates cache",
    )
    invalidated = management_context.client.get(
        "/api/v1/dashboard/summary",
        headers=manager_headers,
    ).json()
    assert invalidated["total_tasks"] == 3
    assert invalidated["generated_at"] != refreshed["generated_at"]
    assert invalidated["range_start"].endswith("Z")
    assert invalidated["range_end"].endswith("Z")
    assert manager["id"] != analyst["id"]


def test_dashboard_archived_and_project_filters(
    management_context: AuthTestContext,
) -> None:
    _, manager_headers = prepare_user(
        management_context,
        "archive-dashboard@example.com",
        "manager",
    )
    first = create_project(
        management_context,
        manager_headers,
        "Visible project",
    )
    second = create_project(
        management_context,
        manager_headers,
        "Archived project",
    )
    create_task(
        management_context,
        manager_headers,
        project_id=first["id"],
        title="Visible task",
    )
    create_task(
        management_context,
        manager_headers,
        project_id=second["id"],
        title="Archived task",
    )
    assert (
        management_context.client.delete(
            f"/api/v1/projects/{second['id']}",
            headers=manager_headers,
        ).status_code
        == 204
    )
    default = management_context.client.get(
        "/api/v1/dashboard/summary",
        headers=manager_headers,
    ).json()
    included = management_context.client.get(
        "/api/v1/dashboard/summary",
        headers=manager_headers,
        params={"include_archived": "true"},
    ).json()
    filtered = management_context.client.get(
        "/api/v1/dashboard/summary",
        headers=manager_headers,
        params={
            "include_archived": "true",
            "project_id": second["id"],
        },
    ).json()
    assert default["total_projects"] == 1
    assert default["total_tasks"] == 1
    assert included["total_projects"] == 2
    assert included["archived_projects"] == 1
    assert included["total_tasks"] == 2
    assert filtered["total_projects"] == 1
    assert filtered["total_tasks"] == 1
