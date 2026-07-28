import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.database.models import (
    Notification,
    NotificationType,
    Task,
)
from app.services.notification import NotificationService
from tests.auth.conftest import AuthTestContext
from tests.management.test_dashboard_analytics import (
    create_project,
    create_task,
)
from tests.management.test_projects_api import prepare_user

UTC = timezone.utc


def test_notification_ownership_lifecycle_and_assignment_integration(
    management_context: AuthTestContext,
) -> None:
    manager, manager_headers = prepare_user(
        management_context,
        "notification-manager@example.com",
        "manager",
    )
    _, analyst_headers = prepare_user(
        management_context,
        "notification-analyst@example.com",
        "analyst",
    )
    viewer, viewer_headers = prepare_user(
        management_context,
        "notification-viewer@example.com",
        "viewer",
    )
    project = create_project(
        management_context,
        manager_headers,
        "Notifications",
    )
    task = create_task(
        management_context,
        manager_headers,
        project_id=project["id"],
        title="Assigned",
        assigned_to=manager["id"],
    )
    manager_list = management_context.client.get(
        "/api/v1/notifications",
        headers=manager_headers,
    ).json()
    assert manager_list["total"] == 1
    notification = manager_list["items"][0]
    assert notification["type"] == "task_assigned"
    assert notification["entity_id"] == task["id"]
    assert "dedupe_key" not in notification

    noop = management_context.client.patch(
        f"/api/v1/tasks/{task['id']}",
        headers=manager_headers,
        json={"assigned_to": manager["id"]},
    )
    assert noop.status_code == 200
    assert (
        management_context.client.get(
            "/api/v1/notifications/unread-count",
            headers=manager_headers,
        ).json()["unread_count"]
        == 1
    )

    first_read = management_context.client.patch(
        f"/api/v1/notifications/{notification['id']}/read",
        headers=manager_headers,
    )
    assert first_read.status_code == 200
    read_at = first_read.json()["read_at"]
    second_read = management_context.client.patch(
        f"/api/v1/notifications/{notification['id']}/read",
        headers=manager_headers,
    )
    assert second_read.status_code == 200
    assert second_read.json()["read_at"] == read_at

    async def create_viewer_notification() -> None:
        async with management_context.session_factory() as session:
            session.add(
                Notification(
                    user_id=UUID(str(viewer["id"])),
                    type=NotificationType.SYSTEM,
                    title="Viewer notification",
                    message="Tenant-scoped notification",
                )
            )
            await session.commit()

    asyncio.run(create_viewer_notification())
    viewer_notification = management_context.client.get(
        "/api/v1/notifications",
        headers=viewer_headers,
    ).json()["items"][0]
    assert (
        management_context.client.patch(
            f"/api/v1/notifications/{viewer_notification['id']}/read",
            headers=analyst_headers,
        ).status_code
        == 404
    )
    assert (
        management_context.client.delete(
            f"/api/v1/notifications/{viewer_notification['id']}",
            headers=analyst_headers,
        ).status_code
        == 404
    )

    read_all = management_context.client.post(
        "/api/v1/notifications/read-all",
        headers=viewer_headers,
    )
    assert read_all.status_code == 200
    assert read_all.json()["updated_count"] == 1
    assert (
        management_context.client.post(
            "/api/v1/notifications/read-all",
            headers=viewer_headers,
        ).json()["updated_count"]
        == 0
    )
    assert (
        management_context.client.delete(
            f"/api/v1/notifications/{viewer_notification['id']}",
            headers=viewer_headers,
        ).status_code
        == 204
    )


def test_project_status_expiry_and_due_notification_generation(
    management_context: AuthTestContext,
) -> None:
    owner, owner_headers = prepare_user(
        management_context,
        "notification-owner@example.com",
        "manager",
    )
    project = create_project(
        management_context,
        owner_headers,
        "Owned status",
        status="planning",
    )
    assert (
        management_context.client.patch(
            f"/api/v1/projects/{project['id']}",
            headers=owner_headers,
            json={"status": "active"},
        ).status_code
        == 200
    )
    status_items = management_context.client.get(
        "/api/v1/notifications",
        headers=owner_headers,
        params={"type": "project_status_changed"},
    ).json()
    assert status_items["total"] == 0

    due_task = create_task(
        management_context,
        owner_headers,
        project_id=project["id"],
        title="Due soon",
        assigned_to=owner["id"],
        due_date=datetime.now(UTC) + timedelta(days=2),
    )

    async def generate_and_expire() -> tuple[int, int]:
        async with management_context.session_factory() as session:
            first = await NotificationService(
                session
            ).create_due_notifications()
        async with management_context.session_factory() as session:
            second = await NotificationService(
                session
            ).create_due_notifications()
        async with management_context.session_factory() as session:
            session.add(
                Notification(
                    user_id=UUID(str(owner["id"])),
                    type=NotificationType.SYSTEM,
                    title="Expired",
                    message="Expired message",
                    expires_at=datetime.now(UTC) - timedelta(seconds=1),
                )
            )
            await session.commit()
        return first, second

    assert asyncio.run(generate_and_expire()) == (1, 0)
    listed = management_context.client.get(
        "/api/v1/notifications",
        headers=owner_headers,
    ).json()
    assert listed["total"] == 2
    assert all(item["title"] != "Expired" for item in listed["items"])
    assert any(
        item["type"] == "task_due_soon"
        and item["entity_id"] == due_task["id"]
        for item in listed["items"]
    )


def test_notification_failure_rolls_back_related_task_write(
    management_context: AuthTestContext,
) -> None:
    owner, owner_headers = prepare_user(
        management_context,
        "rollback-owner@example.com",
        "manager",
    )
    project = create_project(
        management_context,
        owner_headers,
        "Rollback",
    )

    async def exercise() -> None:
        async with management_context.session_factory() as session:
            task = Task(
                title="Must roll back",
                project_id=UUID(str(project["id"])),
                created_by=UUID(str(owner["id"])),
            )
            session.add(task)
            await session.flush()
            for title in ("First", "Duplicate"):
                session.add(
                    Notification(
                        user_id=UUID(str(owner["id"])),
                        type=NotificationType.SYSTEM,
                        title=title,
                        message="Transactional",
                        entity_type="task",
                        entity_id=task.id,
                        dedupe_key="rollback-collision",
                    )
                )
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()
            count = await session.scalar(
                select(func.count(Task.id)).where(
                    Task.title == "Must roll back"
                )
            )
            assert count == 0

    asyncio.run(exercise())


def test_activity_permissions_filters_safe_metadata_and_deleted_tasks(
    management_context: AuthTestContext,
) -> None:
    _, manager_headers = prepare_user(
        management_context,
        "activity-manager@example.com",
        "manager",
    )
    _, other_headers = prepare_user(
        management_context,
        "activity-other@example.com",
        "manager",
    )
    _, analyst_headers = prepare_user(
        management_context,
        "activity-analyst@example.com",
        "analyst",
    )
    _, executive_headers = prepare_user(
        management_context,
        "activity-executive@example.com",
        "executive",
    )
    project = create_project(
        management_context,
        manager_headers,
        "Activity",
    )
    other = create_project(
        management_context,
        other_headers,
        "Hidden activity",
    )
    task = create_task(
        management_context,
        manager_headers,
        project_id=project["id"],
        title="Deleted activity",
    )
    create_task(
        management_context,
        other_headers,
        project_id=other["id"],
        title="Hidden",
    )
    analyst_before = management_context.client.get(
        "/api/v1/activity",
        headers=analyst_headers,
        params={"entity": "task"},
    ).json()
    assert analyst_before["total"] == 0

    assert (
        management_context.client.delete(
            f"/api/v1/tasks/{task['id']}",
            headers=manager_headers,
        ).status_code
        == 204
    )
    manager_activity = management_context.client.get(
        "/api/v1/activity",
        headers=manager_headers,
        params={"project_id": project["id"], "action": "delete"},
    )
    assert manager_activity.status_code == 200
    body = manager_activity.json()
    assert body["total"] == 1
    assert body["items"][0]["entity_id"] == task["id"]
    assert body["items"][0]["summary"] == "deleted task"
    assert body["items"][0]["metadata"] == {
        "project_id": project["id"]
    }

    other_activity = management_context.client.get(
        "/api/v1/activity",
        headers=other_headers,
        params={"project_id": project["id"]},
    ).json()
    assert other_activity["total"] == 0
    analyst_after = management_context.client.get(
        "/api/v1/activity",
        headers=analyst_headers,
        params={"entity": "task", "action": "delete"},
    ).json()
    assert analyst_after["total"] == 0
    isolated_activity = management_context.client.get(
        "/api/v1/activity",
        headers=executive_headers,
        params={"entity": "task", "action": "delete"},
    ).json()
    assert isolated_activity["total"] == 0
    timestamps = [
        item["timestamp"] for item in manager_activity.json()["items"]
    ]
    assert timestamps == sorted(timestamps, reverse=True)
