import asyncio
from datetime import UTC, datetime
from uuid import UUID

from app.database.models import Notification, NotificationType
from tests.management.test_dashboard_analytics import create_project, create_task
from tests.management.test_projects_api import prepare_user


def test_customer_cannot_read_or_mutate_another_customers_portfolio(
    management_context,
) -> None:
    _, customer_a_headers = prepare_user(
        management_context, "adversarial-portfolio-a@example.com", "manager"
    )
    customer_b, customer_b_headers = prepare_user(
        management_context, "adversarial-portfolio-b@example.com", "manager"
    )

    own = management_context.client.get(
        "/api/v1/portfolio", headers=customer_b_headers
    )
    assert own.status_code == 200
    assert own.json()["user_id"] == customer_b["id"]

    cross_account = management_context.client.get(
        f"/api/v1/portfolio/operations/customers/{customer_b['id']}",
        headers=customer_a_headers,
    )
    assert cross_account.status_code in (403, 404)
    if cross_account.content:
        assert "balances" not in cross_account.json()


def test_customer_cannot_read_or_mutate_another_customers_task_or_project(
    management_context,
) -> None:
    _, customer_a_headers = prepare_user(
        management_context, "adversarial-task-a@example.com", "manager"
    )
    _, customer_b_headers = prepare_user(
        management_context, "adversarial-task-b@example.com", "manager"
    )
    project = create_project(management_context, customer_b_headers, "B-only project")
    task = create_task(
        management_context,
        customer_b_headers,
        project_id=project["id"],
        title="B-only task",
    )

    for method, path, payload in (
        ("get", f"/api/v1/tasks/{task['id']}", None),
        ("patch", f"/api/v1/tasks/{task['id']}", {"title": "A takeover"}),
        ("delete", f"/api/v1/tasks/{task['id']}", None),
    ):
        response = getattr(management_context.client, method)(
            path, headers=customer_a_headers, json=payload
        ) if payload is not None else getattr(management_context.client, method)(
            path, headers=customer_a_headers
        )
        assert response.status_code in (403, 404)

    forged_create = management_context.client.post(
        "/api/v1/tasks",
        headers=customer_a_headers,
        json={"title": "Forged A task", "project_id": project["id"]},
    )
    assert forged_create.status_code in (403, 404)

    unchanged = management_context.client.get(
        f"/api/v1/tasks/{task['id']}", headers=customer_b_headers
    )
    assert unchanged.status_code == 200
    assert unchanged.json()["title"] == "B-only task"
    assert (
        management_context.client.get(
            f"/api/v1/projects/{project['id']}", headers=customer_b_headers
        ).json()["name"]
        == "B-only project"
    )


def test_customer_cannot_read_or_mutate_another_customers_notification(
    management_context,
) -> None:
    customer_a, customer_a_headers = prepare_user(
        management_context, "adversarial-notification-a@example.com", "manager"
    )
    customer_b, customer_b_headers = prepare_user(
        management_context, "adversarial-notification-b@example.com", "manager"
    )

    async def create_notification() -> UUID:
        async with management_context.session_factory() as session:
            notification = Notification(
                user_id=UUID(str(customer_b["id"])),
                type=NotificationType.SYSTEM,
                title="B-secret-notification",
                message="B-only message",
            )
            session.add(notification)
            await session.commit()
            return notification.id

    notification_id = asyncio.run(create_notification())
    listed = management_context.client.get(
        "/api/v1/notifications", headers=customer_a_headers
    )
    assert listed.status_code == 200
    assert "B-secret-notification" not in listed.text

    marked = management_context.client.patch(
        f"/api/v1/notifications/{notification_id}/read",
        headers=customer_a_headers,
    )
    deleted = management_context.client.delete(
        f"/api/v1/notifications/{notification_id}",
        headers=customer_a_headers,
    )
    assert marked.status_code in (403, 404)
    assert deleted.status_code in (403, 404)

    still_owned = management_context.client.get(
        "/api/v1/notifications", headers=customer_b_headers
    )
    assert still_owned.status_code == 200
    assert any(item["id"] == str(notification_id) for item in still_owned.json()["items"])
    assert customer_a["id"] != customer_b["id"]


def test_qlab_rejects_foreign_workspace_and_related_resource_access(
    management_context,
) -> None:
    _, customer_a_headers = prepare_user(
        management_context, "adversarial-qlab-a@example.com", "manager"
    )
    _, customer_b_headers = prepare_user(
        management_context, "adversarial-qlab-b@example.com", "manager"
    )
    experiment_response = management_context.client.post(
        "/api/v1/research/qlab/experiments",
        headers=customer_b_headers,
        json={"name": "B-only experiment"},
    )
    assert experiment_response.status_code == 201
    experiment = experiment_response.json()

    forged_experiment = management_context.client.post(
        "/api/v1/research/qlab/experiments",
        headers=customer_a_headers,
        json={"name": "Forged experiment", "workspace_id": experiment["workspace_id"]},
    )
    assert forged_experiment.status_code in (403, 404)

    dataset = management_context.client.post(
        f"/api/v1/research/qlab/experiments/{experiment['id']}/datasets",
        headers=customer_b_headers,
        json={
            "name": "B dataset",
            "source": "B source",
            "observed_at": datetime.now(UTC).isoformat(),
            "provenance": {"source": "adversarial"},
        },
    )
    model = management_context.client.post(
        f"/api/v1/research/qlab/experiments/{experiment['id']}/models",
        headers=customer_b_headers,
        json={"name": "B model", "version": "1", "provenance": {"source": "adversarial"}},
    )
    assert dataset.status_code == 201
    assert model.status_code == 201

    related_to_a = management_context.client.get(
        f"/api/v1/research/qlab/experiments/{experiment['id']}/runs",
        headers=customer_a_headers,
    )
    assert related_to_a.status_code in (403, 404)

    forged_run = management_context.client.post(
        f"/api/v1/research/qlab/experiments/{experiment['id']}/runs",
        headers=customer_a_headers,
        json={"dataset_id": dataset.json()["id"], "model_id": model.json()["id"]},
    )
    assert forged_run.status_code in (403, 404)

    related_to_b = management_context.client.get(
        f"/api/v1/research/qlab/experiments/{experiment['id']}/runs",
        headers=customer_b_headers,
    )
    assert related_to_b.status_code == 200
    assert related_to_b.json() == []


def test_dashboard_and_analytics_ignore_foreign_client_filters(
    management_context,
) -> None:
    customer_a, customer_a_headers = prepare_user(
        management_context, "adversarial-dashboard-a@example.com", "manager"
    )
    customer_b, customer_b_headers = prepare_user(
        management_context, "adversarial-dashboard-b@example.com", "manager"
    )
    project_a = create_project(management_context, customer_a_headers, "A dashboard project")
    project_b = create_project(management_context, customer_b_headers, "B dashboard project")
    create_task(
        management_context, customer_a_headers, project_id=project_a["id"], title="A dashboard task"
    )
    create_task(
        management_context, customer_b_headers, project_id=project_b["id"], title="B dashboard task"
    )

    for headers, foreign_project, foreign_owner, own_name, foreign_name in (
        (customer_a_headers, project_b, customer_b, "A dashboard project", "B dashboard project"),
        (customer_b_headers, project_a, customer_a, "B dashboard project", "A dashboard project"),
    ):
        dashboard = management_context.client.get(
            "/api/v1/dashboard/summary", headers=headers, params={"refresh": "true"}
        )
        assert dashboard.status_code == 200
        assert dashboard.json()["total_projects"] == 1
        assert dashboard.json()["total_tasks"] == 1

        filtered = management_context.client.get(
            "/api/v1/dashboard/summary",
            headers=headers,
            params={"project_id": foreign_project["id"], "owner_id": foreign_owner["id"], "refresh": "true"},
        )
        assert filtered.status_code == 200
        assert filtered.json()["total_projects"] == 0
        assert filtered.json()["total_tasks"] == 0

        projects = management_context.client.get(
            "/api/v1/analytics/projects",
            headers=headers,
            params={"owner_id": foreign_owner["id"], "search": foreign_name},
        )
        assert projects.status_code == 200
        assert projects.json()["total"] == 0
        assert own_name != foreign_name


def test_agent_conversation_owner_and_direct_id_are_account_scoped(
    management_context,
) -> None:
    _, customer_a_headers = prepare_user(
        management_context, "adversarial-agent-a@example.com", "manager"
    )
    _, customer_b_headers = prepare_user(
        management_context, "adversarial-agent-b@example.com", "administrator"
    )
    agent = management_context.client.post(
        "/api/v1/agents",
        headers=customer_b_headers,
        json={
            "slug": "adversarial-b-agent",
            "name": "B private agent",
            "system_instructions": "B-only instructions",
        },
    )
    assert agent.status_code == 201
    active = management_context.client.patch(
        f"/api/v1/agents/{agent.json()['id']}/activate",
        headers=customer_b_headers,
    )
    assert active.status_code == 200

    conversation = management_context.client.post(
        "/api/v1/conversations",
        headers=customer_b_headers,
        json={"agent_id": agent.json()["id"], "title": "B-only conversation"},
    )
    assert conversation.status_code == 201
    conversation_id = conversation.json()["id"]

    listed = management_context.client.get(
        "/api/v1/conversations",
        headers=customer_a_headers,
        params={"owner_id": conversation.json()["owner_id"]},
    )
    assert listed.status_code in (403, 404)
    direct = management_context.client.get(
        f"/api/v1/conversations/{conversation_id}", headers=customer_a_headers
    )
    assert direct.status_code in (403, 404)
    renamed = management_context.client.patch(
        f"/api/v1/conversations/{conversation_id}",
        headers=customer_a_headers,
        json={"title": "A takeover"},
    )
    assert renamed.status_code in (403, 404)
    unchanged = management_context.client.get(
        f"/api/v1/conversations/{conversation_id}", headers=customer_b_headers
    )
    assert unchanged.status_code == 200
    assert unchanged.json()["title"] == "B-only conversation"
