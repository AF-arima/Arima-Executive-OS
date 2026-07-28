from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    AgentRunStatus,
    AuditAction,
    AuditEntity,
    User,
)
from app.execution.engine import ExecutionEngine
from app.execution.estimators import CostEstimator, ZeroPricingStrategy
from app.execution.exceptions import InvalidTransition
from app.execution.policies import RetryPolicy, TimeoutPolicy
from app.execution.providers import MockProviderAdapter, ProviderRegistry
from app.execution.tool_adapters import (
    ToolAdapterRegistry,
    mock_tool_adapters,
)
from app.execution.tools import ToolExecutionEngine
from app.execution.types import (
    ExecutionResult,
    RetryPreparation,
    ToolInvocation,
)
from app.services.audit import record_audit
from app.services.agent import RunService


class ExecutionOrchestrator:
    def __init__(
        self,
        session: AsyncSession,
        engine: ExecutionEngine,
        retry_policy: RetryPolicy,
    ) -> None:
        self.session = session
        self.engine = engine
        self.retry_policy = retry_policy

    @classmethod
    def deterministic(cls, session: AsyncSession) -> ExecutionOrchestrator:
        retry_policy = RetryPolicy(
            max_attempts=2,
            backoff_base_ms=100,
            backoff_factor=2,
        )
        providers = ProviderRegistry((MockProviderAdapter(),))
        tools = ToolAdapterRegistry(mock_tool_adapters())
        tool_engine = ToolExecutionEngine(session, tools)
        engine = ExecutionEngine(
            session,
            providers=providers,
            tool_engine=tool_engine,
            cost_estimator=CostEstimator(ZeroPricingStrategy()),
            retry_policy=retry_policy,
            timeout_policy=TimeoutPolicy(max_duration_ms=300_000),
        )
        return cls(session, engine, retry_policy)

    async def execute_queued(
        self,
        run_id: UUID,
        actor: User,
        *,
        provider_name: str = "mock",
        tool_invocations: tuple[ToolInvocation, ...] = (),
    ) -> ExecutionResult:
        return await self.engine.execute(
            run_id,
            actor,
            provider_name=provider_name,
            tool_invocations=tool_invocations,
        )

    async def resume_after_approval(
        self,
        run_id: UUID,
        actor: User,
        *,
        provider_name: str = "mock",
        tool_invocations: tuple[ToolInvocation, ...],
    ) -> ExecutionResult:
        return await self.engine.resume(
            run_id,
            actor,
            provider_name=provider_name,
            tool_invocations=tool_invocations,
        )

    async def cancel(self, run_id: UUID, actor: User) -> None:
        await self.engine.cancel(run_id, actor)

    async def prepare_retry(
        self,
        run_id: UUID,
        actor: User,
    ) -> RetryPreparation:
        run = await RunService(self.session).get(run_id, actor)
        if run.status is not AgentRunStatus.FAILED:
            raise InvalidTransition("Only failed runs can prepare a retry")
        previous_attempt = run.metadata_.get("attempt_count", 1)
        attempt = (
            previous_attempt + 1
            if isinstance(previous_attempt, int)
            else 2
        )
        retryable = run.failure_code not in {
            "ExecutionCancelled",
            "InvalidTransition",
        }
        record_audit(
            self.session,
            actor_id=actor.id,
            action=AuditAction.UPDATE,
            entity=AuditEntity.AUTOMATION,
            entity_id=run.id,
        )
        await self.session.commit()
        return RetryPreparation(
            previous_run_id=run.id,
            next_attempt=attempt,
            retryable=retryable,
            backoff_metadata=self.retry_policy.backoff_metadata(attempt),
        )
