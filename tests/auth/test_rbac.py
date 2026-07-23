from tests.auth.conftest import AuthTestContext
from tests.auth.helpers import (
    bearer,
    grant_role,
    login_user,
    register_user,
)


def test_non_administrator_is_rejected(
    auth_context: AuthTestContext,
) -> None:
    target = register_user(auth_context, "target@example.com")
    register_user(auth_context, "viewer@example.com")
    viewer_tokens = login_user(auth_context, "viewer@example.com")

    response = auth_context.client.post(
        f"/api/v1/admin/users/{target['id']}/roles",
        headers=bearer(viewer_tokens["access_token"]),
        json={"role_name": "analyst"},
    )

    assert response.status_code == 403


def test_administrator_assigns_and_removes_role_idempotently(
    auth_context: AuthTestContext,
) -> None:
    target = register_user(auth_context, "target@example.com")
    register_user(auth_context, "admin@example.com")
    grant_role(auth_context, "admin@example.com", "administrator")
    admin_tokens = login_user(auth_context, "admin@example.com")
    headers = bearer(admin_tokens["access_token"])
    endpoint = f"/api/v1/admin/users/{target['id']}/roles"

    assigned = auth_context.client.post(
        endpoint,
        headers=headers,
        json={"role_name": "manager"},
    )
    duplicate = auth_context.client.post(
        endpoint,
        headers=headers,
        json={"role_name": "manager"},
    )
    removed = auth_context.client.delete(
        f"{endpoint}/manager",
        headers=headers,
    )
    removed_again = auth_context.client.delete(
        f"{endpoint}/manager",
        headers=headers,
    )

    assert assigned.status_code == 200
    assert duplicate.status_code == 200
    assert {role["name"] for role in assigned.json()["roles"]} == {
        "manager",
        "viewer",
    }
    assert removed.status_code == 204
    assert removed_again.status_code == 204
