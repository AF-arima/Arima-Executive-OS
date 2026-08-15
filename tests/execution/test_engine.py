import asyncio
from dataclasses import dataclass
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import func, select

from app.database.models import (
    AgentApproval,
    AgentApprovalStatus,
    AgentContextSnapshot,
    AgentMessage,
    AgentRun,
    AgentRunStatus,
    AgentToolExecution,
    AuditLog,
    Notification,
)
from app.database.repositories import UserRepository
from app.execution import (
    ApprovalRequired,
    CostEstimator,
    ExecutionEngine,
    InvalidTransition,
    ProviderFailure,
    ProviderHealth,
    ProviderRegistry,
    ProviderRequest,
    ProviderResponse,
    RetryExhausted,
    RetryPolicy,
    StructuredPrompt,
    ToolAdapterRegistry,
    ToolExecutionEngine,
    ToolInvocation,
    ZeroPricingStrategy,
    mock_tool_adapters,
)
from app.services.agent_bootstrap import bootstrap_agent_platform
from app.services.agent_execution import ExecutionOrchestrator
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


def prepare_execution(
    context: AuthTestContext,
    *,
    suffix: str,
    owner_role: str = "analyst",
) -> tuple[
    dict[str, object],
    dict[str, str],
    dict[str, str],
    UUID,
    UUID,
]:
    owner, owner_headers = prepare_user(
        context,
        f"execution-owner-{suffix}@example.com",
        owner_role,
    )
    admin, admin_headers = prepare_user(
        context,
        f"execution-admin-{suffix}@example.com",
        "administrator",
    )

    async def bootstrap() -> UUID:
        async with context.session_factory() as session:
            result = await bootstrap_agent_platform(
                session,
                created_by_id=UUID(str(admin["id"])),
            )
            return result.agent.id

    agent_id = asyncio.run(bootstrap())
    conversation = context.client.post(
        "/api/v1/conversations",
        headers=owner_headers,
        json={
            "agent_id": str(agent_id),
            "title": f"Execution {suffix}",
        },
    )
    assert conversation.status_code == 201, conversation.text
    conversation_id = UUID(conversation.json()["id"])
    message = context.client.post(
        "/api/v1/messages",
        headers=owner_headers,
        json={
            "conversation_id": str(conversation_id),
            "role": "user",
            "content": f"Prepare deterministic response {suffix}",
        },
    )
    assert message.status_code == 201, message.text
    run = context.client.post(
        "/api/v1/runs",
        headers=owner_headers,
        json={
            "conversation_id": str(conversation_id),
            "input_message_id": message.json()["id"],
        },
    )
    assert run.status_code == 201, run.text
    return (
        owner,
        owner_headers,
        admin_headers,
        UUID(run.json()["id"]),
        conversation_id,
    )


def test_execution_lifecycle_context_prompt_tool_metrics_audit_notification(
    management_context: AuthTestContext,
) -> None:
    owner, _, _, run_id, conversation_id = prepare_execution(
        management_context,
        suffix="success",
    )

    async def execute_and_inspect() -> tuple[object, dict[str, int]]:
        async with management_context.session_factory() as session:
            actor = await UserRepository(session).get_with_roles(
                UUID(str(owner["id"]))
            )
            assert actor is not None
            result = await ExecutionOrchestrator.deterministic(
                session
            ).execute_queued(
                run_id,
                actor,
                tool_invocations=(
                    ToolInvocation(
                        slug="projects.read",
                        payload={"limit": 5},
                    ),
                ),
            )
            run = await session.get(AgentRun, run_id)
            assert run is not None
            assert run.status is AgentRunStatus.COMPLETED
            assert run.model_provider == "mock"
            assert run.total_tokens == (
                result.prompt_tokens + result.completion_tokens
            )
            assert run.estimated_cost_gbp == Decimal("0.000000")
            snapshot_count = await session.scalar(
                select(func.count(AgentContextSnapshot.id)).where(
                    AgentContextSnapshot.run_id == run_id
                )
            )
            assistant_count = await session.scalar(
                select(func.count(AgentMessage.id)).where(
                    AgentMessage.conversation_id == conversation_id,
                    AgentMessage.run_id == run_id,
                )
            )
            tool_count = await session.scalar(
                select(func.count(AgentToolExecution.id)).where(
                    AgentToolExecution.run_id == run_id
                )
            )
            audit_count = await session.scalar(
                select(func.count(AuditLog.id)).where(
                    AuditLog.entity_id.in_(
                        [
                            run_id,
                            result.output_message_id,
                            result.tool_results[0].execution_id,
                        ]
                    )
                )
            )
            notification_count = await session.scalar(
                select(func.count(Notification.id)).where(
                    Notification.user_id == UUID(str(owner["id"])),
                    Notification.entity_type.in_(
                        ["agent_run", "agent_tool_execution"]
                    ),
                )
            )
            return result, {
                "snapshot": int(snapshot_count or 0),
                "assistant": int(assistant_count or 0),
                "tool": int(tool_count or 0),
                "audit": int(audit_count or 0),
                "notification": int(notification_count or 0),
            }

    result, counts = asyncio.run(execute_and_inspect())
    assert result.provider_name == "mock"
    assert counts == {
        "snapshot": 1,
        "assistant": 1,
        "tool": 1,
        "audit": 6,
        "notification": 2,
    }


def test_approval_pause_resume_and_approval_cancellation(
    management_context: AuthTestContext,
) -> None:
    owner, owner_headers, admin_headers, run_id, _ = prepare_execution(
        management_context,
        suffix="approval",
        owner_role="manager",
    )

    async def pause() -> ApprovalRequired:
        async with management_context.session_factory() as session:
            actor = await UserRepository(session).get_with_roles(
                UUID(str(owner["id"]))
            )
            assert actor is not None
            with pytest.raises(ApprovalRequired) as caught:
                await ExecutionOrchestrator.deterministic(
                    session
                ).execute_queued(
                    run_id,
                    actor,
                    tool_invocations=(
                        ToolInvocation(
                            slug="memory.write",
                            payload={"key": "preference", "value": "concise"},
                        ),
                    ),
                )
            run = await session.get(AgentRun, run_id)
            assert run is not None
            assert run.status is AgentRunStatus.WAITING_FOR_APPROVAL
            return caught.value

    required = asyncio.run(pause())
    assert (
        management_context.client.patch(
            f"/api/v1/approvals/{required.approval_id}",
            headers=admin_headers,
            json={"status": "approved"},
        ).status_code
        == 404
    )
    approved = management_context.client.patch(
        f"/api/v1/approvals/{required.approval_id}",
        headers=owner_headers,
        json={"status": "approved"},
    )
    assert approved.status_code == 200, approved.text

    async def resume() -> None:
        async with management_context.session_factory() as session:
            actor = await UserRepository(session).get_with_roles(
                UUID(str(owner["id"]))
            )
            assert actor is not None
            result = await ExecutionOrchestrator.deterministic(
                session
            ).resume_after_approval(
                run_id,
                actor,
                tool_invocations=(
                    ToolInvocation(
                        slug="memory.write",
                        payload={"key": "preference", "value": "concise"},
                        execution_id=required.execution_id,
                        approval_id=required.approval_id,
                    ),
                ),
            )
            assert len(result.tool_results) == 1
            execution = await session.get(
                AgentToolExecution,
                required.execution_id,
            )
            assert execution is not None
            assert execution.status.value == "succeeded"
            assert execution.output_payload is not None
            assert execution.output_payload["mutated"] is False

    asyncio.run(resume())

    cancel_owner, _, _, cancel_run_id, _ = prepare_execution(
        management_context,
        suffix="cancel-approval",
    )

    async def pause_and_cancel() -> None:
        async with management_context.session_factory() as session:
            actor = await UserRepository(session).get_with_roles(
                UUID(str(cancel_owner["id"]))
            )
            assert actor is not None
            orchestrator = ExecutionOrchestrator.deterministic(session)
            with pytest.raises(ApprovalRequired) as caught:
                await orchestrator.execute_queued(
                    cancel_run_id,
                    actor,
                    tool_invocations=(
                        ToolInvocation(
                            slug="memory.write",
                            payload={"key": "cancelled"},
                        ),
                    ),
                )
            await orchestrator.cancel(cancel_run_id, actor)
            run = await session.get(AgentRun, cancel_run_id)
            approval = await session.get(
                AgentApproval,
                caught.value.approval_id,
            )
            execution = await session.get(
                AgentToolExecution,
                caught.value.execution_id,
            )
            assert run is not None
            assert approval is not None
            assert execution is not None
            assert run.status is AgentRunStatus.CANCELLED
            assert approval.status is AgentApprovalStatus.CANCELLED
            assert execution.status.value == "cancelled"

    asyncio.run(pause_and_cancel())


@dataclass
class FailingProvider:
    name: str = "failing"
    attempts: int = 0

    async def prepare(self, request: ProviderRequest) -> ProviderRequest:
        return request

    async def execute(self, request: ProviderRequest) -> ProviderResponse:
        del request
        self.attempts += 1
        raise ProviderFailure("deterministic failure", retryable=True)

    async def cancel(self, run_id: UUID) -> None:
        del run_id

    def estimate_cost(
        self,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> Decimal:
        del prompt_tokens, completion_tokens
        return Decimal("0")

    def estimate_tokens(self, prompt: StructuredPrompt) -> int:
        del prompt
        return 1

    async def health(self) -> ProviderHealth:
        return ProviderHealth(available=True)


def test_provider_failure_retry_exhaustion_retry_preparation_and_cancel(
    management_context: AuthTestContext,
) -> None:
    owner, _, _, run_id, _ = prepare_execution(
        management_context,
        suffix="failure",
    )
    provider = FailingProvider()

    async def fail_and_prepare() -> None:
        async with management_context.session_factory() as session:
            actor = await UserRepository(session).get_with_roles(
                UUID(str(owner["id"]))
            )
            assert actor is not None
            retry = RetryPolicy(max_attempts=2)
            engine = ExecutionEngine(
                session,
                providers=ProviderRegistry((provider,)),
                tool_engine=ToolExecutionEngine(
                    session,
                    ToolAdapterRegistry(mock_tool_adapters()),
                ),
                cost_estimator=CostEstimator(ZeroPricingStrategy()),
                retry_policy=retry,
            )
            orchestrator = ExecutionOrchestrator(session, engine, retry)
            with pytest.raises(RetryExhausted):
                await orchestrator.execute_queued(
                    run_id,
                    actor,
                    provider_name="failing",
                )
            run = await session.get(AgentRun, run_id)
            assert run is not None
            assert run.status is AgentRunStatus.FAILED
            assert run.failure_code == "RetryExhausted"
            assert run.failure_message == (
                "Agent execution failed (RetryExhausted)"
            )
            assert "deterministic failure" not in run.failure_message
            preparation = await orchestrator.prepare_retry(run_id, actor)
            assert preparation.next_attempt == 2
            assert preparation.retryable is True
            assert preparation.backoff_metadata["sleep_performed"] is False

    asyncio.run(fail_and_prepare())
    assert provider.attempts == 2

    cancel_owner, _, _, cancel_run_id, _ = prepare_execution(
        management_context,
        suffix="queued-cancel",
    )

    async def cancel_queued() -> None:
        async with management_context.session_factory() as session:
            actor = await UserRepository(session).get_with_roles(
                UUID(str(cancel_owner["id"]))
            )
            assert actor is not None
            orchestrator = ExecutionOrchestrator.deterministic(session)
            await orchestrator.cancel(cancel_run_id, actor)
            run = await session.get(AgentRun, cancel_run_id)
            assert run is not None
            assert run.status is AgentRunStatus.CANCELLED
            with pytest.raises(InvalidTransition):
                await orchestrator.cancel(cancel_run_id, actor)

    asyncio.run(cancel_queued())

    (
        running_owner,
        running_headers,
        _,
        running_run_id,
        _,
    ) = prepare_execution(
        management_context,
        suffix="running-cancel",
    )
    started = management_context.client.patch(
        f"/api/v1/runs/{running_run_id}",
        headers=running_headers,
        json={"status": "running"},
    )
    assert started.status_code == 200

    async def cancel_running() -> None:
        async with management_context.session_factory() as session:
            actor = await UserRepository(session).get_with_roles(
                UUID(str(running_owner["id"]))
            )
            assert actor is not None
            await ExecutionOrchestrator.deterministic(session).cancel(
                running_run_id,
                actor,
            )
            run = await session.get(AgentRun, running_run_id)
            assert run is not None
            assert run.status is AgentRunStatus.CANCELLED

    asyncio.run(cancel_running())
