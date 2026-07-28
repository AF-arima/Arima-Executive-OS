import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID

from app.database.models import Project
from tests.auth.conftest import AuthTestContext
from tests.management.test_dashboard_analytics import (
    create_project,
    create_task,
)
from tests.management.test_projects_api import prepare_user

UTC = timezone.utc


def test_project_and_task_analytics_aggregates_and_series(
    management_context: AuthTestContext,
) -> None:
    manager, manager_headers = prepare_user(
        management_context,
        "analytics-manager@example.com",
        "manager",
    )
    project = create_project(
        management_context,
        manager_headers,
        "Alpha_100% Analytics",
    )
    now = datetime.now(UTC)
    create_task(
        management_context,
        manager_headers,
        project_id=project["id"],
        title="Done",
        assigned_to=manager["id"],
        status="completed",
        priority="high",
    )
    create_task(
        management_context,
        manager_headers,
        project_id=project["id"],
        title="Late",
        assigned_to=manager["id"],
        priority="urgent",
        due_date=now - timedelta(days=1),
    )
    create_task(
        management_context,
        manager_headers,
        project_id=project["id"],
        title="Unassigned",
        due_date=now + timedelta(days=3),
    )
    project_response = management_context.client.get(
        "/api/v1/analytics/projects",
        headers=manager_headers,
        params={
            "search": "_100%",
            "sort_by": "completion_rate",
            "direction": "desc",
            "limit": 1,
        },
    )
    assert project_response.status_code == 200
    body = project_response.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["total_tasks"] == 3
    assert item["completed_tasks"] == 1
    assert item["overdue_tasks"] == 1
    assert item["unassigned_tasks"] == 1
    assert item["completion_rate"] == 0.333333
    assert item["last_activity_at"] is not None

    start = (now - timedelta(days=2)).isoformat()
    end = (now + timedelta(days=1)).isoformat()
    for interval in ("day", "week", "month"):
        response = management_context.client.get(
            "/api/v1/analytics/tasks",
            headers=manager_headers,
            params={
                "start_date": start,
                "end_date": end,
                "project_id": project["id"],
                "interval": interval,
            },
        )
        assert response.status_code == 200
        analytics = response.json()
        assert analytics["totals"] == 3
        assert analytics["completed_count"] == 1
        assert analytics["overdue_count"] == 1
        assert analytics["status_breakdown"]["completed"] == 1
        assert analytics["priority_breakdown"]["urgent"] == 1
        assert sum(
            point["value"] for point in analytics["created_series"]
        ) == 3
        assert analytics["throughput_series"]
        assert analytics["overdue_series"]
        if interval == "day":
            assert len(analytics["created_series"]) == 4
            assert any(
                point["value"] == 0
                for point in analytics["created_series"]
            )
    filtered = management_context.client.get(
        "/api/v1/analytics/tasks",
        headers=manager_headers,
        params={
            "start_date": start,
            "end_date": end,
            "project_id": project["id"],
            "assigned_to": manager["id"],
            "status": "completed",
            "priority": "high",
        },
    ).json()
    assert filtered["totals"] == 1


def test_analytics_range_limits_sorting_pagination_and_visibility(
    management_context: AuthTestContext,
) -> None:
    manager, manager_headers = prepare_user(
        management_context,
        "scope-manager@example.com",
        "manager",
    )
    _, other_headers = prepare_user(
        management_context,
        "scope-other@example.com",
        "manager",
    )
    own = create_project(
        management_context,
        manager_headers,
        "Zulu own",
    )
    alpha = create_project(
        management_context,
        manager_headers,
        "Alpha own",
    )
    create_project(
        management_context,
        other_headers,
        "Hidden other",
    )
    create_task(
        management_context,
        manager_headers,
        project_id=own["id"],
        title="Scoped",
    )
    page = management_context.client.get(
        "/api/v1/analytics/projects",
        headers=manager_headers,
        params={
            "sort_by": "name",
            "direction": "asc",
            "limit": 1,
            "offset": 1,
        },
    ).json()
    assert page["total"] == 2
    assert page["items"][0]["name"] == "Zulu own"

    async def add_project_without_activity() -> None:
        async with management_context.session_factory() as session:
            session.add(
                Project(
                    name="No activity",
                    owner_id=UUID(str(manager["id"])),
                    created_by=UUID(str(manager["id"])),
                )
            )
            await session.commit()

    asyncio.run(add_project_without_activity())
    null_sorted = management_context.client.get(
        "/api/v1/analytics/projects",
        headers=manager_headers,
        params={
            "sort_by": "last_activity_at",
            "direction": "desc",
        },
    ).json()
    assert null_sorted["items"][-1]["name"] == "No activity"

    assert (
        management_context.client.delete(
            f"/api/v1/projects/{alpha['id']}",
            headers=manager_headers,
        ).status_code
        == 204
    )
    excluded = management_context.client.get(
        "/api/v1/analytics/projects",
        headers=manager_headers,
    ).json()
    included = management_context.client.get(
        "/api/v1/analytics/projects",
        headers=manager_headers,
        params={"include_archived": "true"},
    ).json()
    assert excluded["total"] == 2
    assert included["total"] == 3
    assert any(item["archived"] for item in included["items"])

    too_long = management_context.client.get(
        "/api/v1/analytics/tasks",
        headers=manager_headers,
        params={
            "start_date": "2020-01-01T00:00:00Z",
            "end_date": "2021-01-01T00:00:00Z",
            "interval": "day",
        },
    )
    assert too_long.status_code == 422
    assert (
        management_context.client.get(
            "/api/v1/analytics/tasks",
            headers=manager_headers,
            params={"interval": "quarter"},
        ).status_code
        == 422
    )


def test_workload_formula_and_identity_scope(
    management_context: AuthTestContext,
) -> None:
    manager, manager_headers = prepare_user(
        management_context,
        "workload-manager@example.com",
        "manager",
    )
    _, viewer_headers = prepare_user(
        management_context,
        "workload-viewer@example.com",
        "viewer",
    )
    project = create_project(
        management_context,
        manager_headers,
        "Workload",
    )
    create_task(
        management_context,
        manager_headers,
        project_id=project["id"],
        title="Urgent overdue",
        assigned_to=manager["id"],
        priority="urgent",
        due_date=datetime.now(UTC) - timedelta(days=1),
    )
    response = management_context.client.get(
        "/api/v1/analytics/workload",
        headers=manager_headers,
        params={
            "project_id": project["id"],
            "sort_by": "workload_score",
            "direction": "desc",
        },
    )
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["email"] == "workload-manager@example.com"
    assert item["active_task_count"] == 1
    assert item["overdue_task_count"] == 1
    assert item["urgent_task_count"] == 1
    assert item["workload_score"] == 6

    viewer = management_context.client.get(
        "/api/v1/analytics/workload",
        headers=viewer_headers,
    ).json()
    assert viewer["total"] == 0
    assert (
        management_context.client.get(
            "/api/v1/analytics/workload",
            headers=manager_headers,
            params={"role": "   "},
        ).status_code
        == 422
    )
