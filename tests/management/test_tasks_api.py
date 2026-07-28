import asyncio
from uuid import UUID

from sqlalchemy import select

from app.database.models import AuditAction, AuditEntity, AuditLog
from tests.auth.conftest import AuthTestContext
from tests.management.test_projects_api import prepare_user


def test_task_lifecycle_assignment_permissions_and_audit(
    management_context: AuthTestContext,
) -> None:
    manager, manager_headers = prepare_user(
        management_context,
        "task-manager@example.com",
        "manager",
    )
    analyst, _ = prepare_user(
        management_context,
        "analyst@example.com",
        "analyst",
    )
    viewer, viewer_headers = prepare_user(
        management_context,
        "task-viewer@example.com",
        "viewer",
    )
    _, other_manager_headers = prepare_user(
        management_context,
        "other-manager@example.com",
        "manager",
    )
    project_response = management_context.client.post(
        "/api/v1/projects",
        headers=manager_headers,
        json={"name": "Delivery", "status": "active"},
    )
    assert project_response.status_code == 201
    project_id = project_response.json()["id"]

    cross_workspace_assignment = management_context.client.post(
        "/api/v1/tasks",
        headers=manager_headers,
        json={
            "title": "Ship report",
            "description": "Executive reporting",
            "priority": "high",
            "project_id": project_id,
            "assigned_to": analyst["id"],
        },
    )
    assert cross_workspace_assignment.status_code == 403

    created = management_context.client.post(
        "/api/v1/tasks",
        headers=manager_headers,
        json={
            "title": "Ship report",
            "description": "Executive reporting",
            "priority": "high",
            "project_id": project_id,
        },
    )
    assert created.status_code == 201
    task = created.json()
    task_id = task["id"]
    assert task["assigned_to"] is None
    assert task["created_by"] == manager["id"]

    denied = management_context.client.patch(
        f"/api/v1/tasks/{task_id}",
        headers=other_manager_headers,
        json={"status": "in_progress"},
    )
    assert denied.status_code == 404
    other_workspace_tasks = management_context.client.get(
        "/api/v1/tasks",
        headers=other_manager_headers,
    )
    assert other_workspace_tasks.status_code == 200
    assert other_workspace_tasks.json()["items"] == []
    cross_workspace_reassign = management_context.client.patch(
        f"/api/v1/tasks/{task_id}",
        headers=manager_headers,
        json={"assigned_to": viewer["id"]},
    )
    assert cross_workspace_reassign.status_code == 403

    completed = management_context.client.patch(
        f"/api/v1/tasks/{task_id}",
        headers=manager_headers,
        json={"status": "completed"},
    )
    assert completed.status_code == 200
    assert completed.json()["completed_at"] is not None
    reopened = management_context.client.patch(
        f"/api/v1/tasks/{task_id}",
        headers=manager_headers,
        json={"status": "in_progress"},
    )
    assert reopened.status_code == 200
    assert reopened.json()["completed_at"] is None

    reassigned = management_context.client.patch(
        f"/api/v1/tasks/{task_id}",
        headers=manager_headers,
        json={"assigned_to": manager["id"], "priority": "urgent"},
    )
    assert reassigned.status_code == 200
    assert reassigned.json()["assigned_to"] == manager["id"]
    assert (
        management_context.client.patch(
            f"/api/v1/tasks/{task_id}",
            headers=other_manager_headers,
            json={"title": "No longer assigned"},
        ).status_code
        == 404
    )
    assert (
        management_context.client.get(
            f"/api/v1/tasks/{task_id}",
            headers=viewer_headers,
        ).status_code
        == 404
    )
    assert (
        management_context.client.delete(
            f"/api/v1/tasks/{task_id}",
            headers=viewer_headers,
        ).status_code
        == 404
    )
    deleted = management_context.client.delete(
        f"/api/v1/tasks/{task_id}",
        headers=manager_headers,
    )
    assert deleted.status_code == 204

    async def get_actions() -> list[AuditAction]:
        async with management_context.session_factory() as session:
            actions = await session.scalars(
                select(AuditLog.action)
                .where(
                    AuditLog.entity == AuditEntity.TASK,
                    AuditLog.entity_id == UUID(task_id),
                )
                .order_by(AuditLog.timestamp, AuditLog.id)
            )
            return list(actions)

    actions = asyncio.run(get_actions())
    assert actions.count(AuditAction.CREATE) == 1
    assert actions.count(AuditAction.STATUS_CHANGE) == 2
    assert actions.count(AuditAction.ASSIGNMENT) == 1
    assert actions.count(AuditAction.DELETE) == 1


def test_task_filters_search_sort_pagination_and_archived_project(
    management_context: AuthTestContext,
) -> None:
    manager, manager_headers = prepare_user(
        management_context,
        "filter-manager@example.com",
        "manager",
    )
    project = management_context.client.post(
        "/api/v1/projects",
        headers=manager_headers,
        json={"name": "Filtered tasks"},
    ).json()
    task_payloads = (
        {
            "title": "Alpha urgent",
            "description": "Needle in roadmap",
            "status": "in_progress",
            "priority": "urgent",
            "project_id": project["id"],
            "assigned_to": manager["id"],
            "due_date": "2020-01-01T00:00:00Z",
        },
        {
            "title": "Beta high",
            "description": "Needle again",
            "status": "completed",
            "priority": "high",
            "project_id": project["id"],
            "assigned_to": manager["id"],
        },
        {
            "title": "Gamma low",
            "priority": "low",
            "project_id": project["id"],
        },
    )
    task_ids: list[str] = []
    for payload in task_payloads:
        response = management_context.client.post(
            "/api/v1/tasks",
            headers=manager_headers,
            json=payload,
        )
        assert response.status_code == 201
        task_ids.append(response.json()["id"])

    listed = management_context.client.get(
        "/api/v1/tasks",
        headers=manager_headers,
        params={
            "project": project["id"],
            "assigned_to": manager["id"],
            "search": "NEEDLE",
            "completed": "false",
            "overdue": "true",
            "sort_by": "priority",
            "direction": "desc",
            "limit": 1,
            "offset": 0,
        },
    )
    assert listed.status_code == 200
    body = listed.json()
    assert body["total"] == 1
    assert body["items"][0]["title"] == "Alpha urgent"

    priorities = management_context.client.get(
        "/api/v1/tasks",
        headers=manager_headers,
        params={
            "project": project["id"],
            "sort_by": "priority",
            "direction": "desc",
            "limit": 2,
        },
    ).json()
    assert priorities["total"] == 3
    assert [item["priority"] for item in priorities["items"]] == [
        "urgent",
        "high",
    ]
    filtered = management_context.client.get(
        "/api/v1/tasks",
        headers=manager_headers,
        params={
            "project": project["id"],
            "status": "completed",
            "priority": "high",
        },
    ).json()
    assert filtered["total"] == 1
    assert filtered["items"][0]["title"] == "Beta high"
    assert (
        management_context.client.post(
            "/api/v1/tasks",
            headers=manager_headers,
            json={
                "title": "Naive date",
                "project_id": project["id"],
                "due_date": "2030-01-01T00:00:00",
            },
        ).status_code
        == 422
    )

    assert (
        management_context.client.delete(
            f"/api/v1/projects/{project['id']}",
            headers=manager_headers,
        ).status_code
        == 204
    )
    assert (
        management_context.client.patch(
            f"/api/v1/tasks/{task_ids[0]}",
            headers=manager_headers,
            json={"title": "Archived mutation"},
        ).status_code
        == 409
    )
    assert (
        management_context.client.post(
            "/api/v1/tasks",
            headers=manager_headers,
            json={
                "title": "Archived create",
                "project_id": project["id"],
            },
        ).status_code
        == 409
    )


def test_manager_cannot_manage_tasks_in_another_project(
    management_context: AuthTestContext,
) -> None:
    _, owner_headers = prepare_user(
        management_context,
        "project-manager@example.com",
        "manager",
    )
    _, outsider_headers = prepare_user(
        management_context,
        "outsider-manager@example.com",
        "manager",
    )
    project = management_context.client.post(
        "/api/v1/projects",
        headers=owner_headers,
        json={"name": "Private project"},
    ).json()
    outsider_project = management_context.client.post(
        "/api/v1/projects",
        headers=outsider_headers,
        json={"name": "Outsider project"},
    ).json()

    response = management_context.client.post(
        "/api/v1/tasks",
        headers=outsider_headers,
        json={"title": "Privilege escalation", "project_id": project["id"]},
    )
    assert response.status_code == 404

    task = management_context.client.post(
        "/api/v1/tasks",
        headers=owner_headers,
        json={"title": "Owned task", "project_id": project["id"]},
    ).json()
    move = management_context.client.patch(
        f"/api/v1/tasks/{task['id']}",
        headers=owner_headers,
        json={"project_id": outsider_project["id"]},
    )
    assert move.status_code == 403
