import asyncio
from uuid import UUID

from sqlalchemy import select

from app.database.models import AuditAction, AuditEntity, AuditLog
from tests.auth.conftest import AuthTestContext
from tests.auth.helpers import (
    bearer,
    grant_role,
    login_user,
    register_user,
)


def prepare_user(
    context: AuthTestContext,
    email: str,
    role: str,
) -> tuple[dict[str, object], dict[str, str]]:
    user = register_user(context, email)
    grant_role(context, email, role)
    token = login_user(context, email)["access_token"]
    return user, bearer(token)


def test_project_crud_permissions_and_archive_read_only(
    management_context: AuthTestContext,
) -> None:
    manager, manager_headers = prepare_user(
        management_context,
        "manager@example.com",
        "manager",
    )
    _, other_manager_headers = prepare_user(
        management_context,
        "other-manager@example.com",
        "manager",
    )
    _, viewer_headers = prepare_user(
        management_context,
        "viewer@example.com",
        "viewer",
    )

    create = management_context.client.post(
        "/api/v1/projects",
        headers=manager_headers,
        json={
            "name": "Quarterly Plan",
            "description": "Alpha roadmap",
            "status": "planning",
        },
    )
    assert create.status_code == 201
    project = create.json()
    project_id = project["id"]
    assert project["owner_id"] == manager["id"]
    assert project["created_by"] == manager["id"]
    assert project["archived_at"] is None

    duplicate = management_context.client.post(
        "/api/v1/projects",
        headers=manager_headers,
        json={"name": " quarterly plan "},
    )
    assert duplicate.status_code == 409

    denied_update = management_context.client.patch(
        f"/api/v1/projects/{project_id}",
        headers=other_manager_headers,
        json={"name": "Stolen"},
    )
    assert denied_update.status_code == 404
    cross_owner_create = management_context.client.post(
        "/api/v1/projects",
        headers=other_manager_headers,
        json={"name": "Other workspace", "owner_id": manager["id"]},
    )
    assert cross_owner_create.status_code == 403
    viewer_create = management_context.client.post(
        "/api/v1/projects",
        headers=viewer_headers,
        json={"name": "Not allowed"},
    )
    assert viewer_create.status_code == 403

    update = management_context.client.patch(
        f"/api/v1/projects/{project_id}",
        headers=manager_headers,
        json={"name": "Quarterly Execution", "status": "active"},
    )
    assert update.status_code == 200
    assert update.json()["status"] == "active"

    viewer_read = management_context.client.get(
        f"/api/v1/projects/{project_id}",
        headers=viewer_headers,
    )
    assert viewer_read.status_code == 404
    other_workspace_projects = management_context.client.get(
        "/api/v1/projects",
        headers=other_manager_headers,
    )
    assert other_workspace_projects.status_code == 200
    assert other_workspace_projects.json()["items"] == []

    archive = management_context.client.delete(
        f"/api/v1/projects/{project_id}",
        headers=manager_headers,
    )
    assert archive.status_code == 204
    archived = management_context.client.get(
        f"/api/v1/projects/{project_id}",
        headers=manager_headers,
    )
    assert archived.status_code == 200
    assert archived.json()["archived_at"] is not None
    read_only = management_context.client.patch(
        f"/api/v1/projects/{project_id}",
        headers=manager_headers,
        json={"description": "Cannot change"},
    )
    assert read_only.status_code == 409

    async def get_actions() -> list[AuditAction]:
        async with management_context.session_factory() as session:
            actions = await session.scalars(
                select(AuditLog.action)
                .where(
                    AuditLog.entity == AuditEntity.PROJECT,
                    AuditLog.entity_id == UUID(project_id),
                )
                .order_by(AuditLog.timestamp, AuditLog.id)
            )
            return list(actions)

    assert asyncio.run(get_actions()) == [
        AuditAction.CREATE,
        AuditAction.UPDATE,
        AuditAction.STATUS_CHANGE,
        AuditAction.DELETE,
    ]


def test_project_filters_search_sort_pagination_and_validation(
    management_context: AuthTestContext,
) -> None:
    executive, executive_headers = prepare_user(
        management_context,
        "executive@example.com",
        "executive",
    )
    for payload in (
        {
            "name": "Zulu",
            "description": "Operations",
            "status": "active",
            "owner_id": executive["id"],
        },
        {
            "name": "Alpha",
            "description": "Operations plan",
            "status": "active",
            "owner_id": executive["id"],
        },
        {
            "name": "Beta",
            "description": "Finance",
            "status": "planning",
            "owner_id": executive["id"],
        },
    ):
        response = management_context.client.post(
            "/api/v1/projects",
            headers=executive_headers,
            json=payload,
        )
        assert response.status_code == 201

    listed = management_context.client.get(
        "/api/v1/projects",
        headers=executive_headers,
        params={
            "status": "active",
            "owner": executive["id"],
            "search": "OPERATIONS",
            "sort_by": "name",
            "direction": "asc",
            "limit": 1,
            "offset": 1,
        },
    )
    assert listed.status_code == 200
    body = listed.json()
    assert body["total"] == 2
    assert body["limit"] == 1
    assert body["offset"] == 1
    assert [item["name"] for item in body["items"]] == ["Zulu"]

    assert (
        management_context.client.get(
            "/api/v1/projects",
            headers=executive_headers,
            params={"limit": 0},
        ).status_code
        == 422
    )
    assert (
        management_context.client.post(
            "/api/v1/projects",
            headers=executive_headers,
            json={"name": " ", "hashed_password": "never accepted"},
        ).status_code
        == 422
    )
    assert (
        management_context.client.get("/api/v1/projects").status_code
        == 401
    )
