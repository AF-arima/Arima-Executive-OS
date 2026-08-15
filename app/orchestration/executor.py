from __future__ import annotations

from app.background.context import BackgroundExecutionContext
from app.core.redaction import safe_failure_detail
from app.background.schemas import (
    BackgroundExecutionRequest,
    BackgroundPermission,
    BackgroundTriggerSource,
)
from app.integrations.context import IntegrationExecutionContext
from app.integrations.schemas import (
    IntegrationPermission,
    IntegrationRequest,
)
from app.orchestration.context import OrchestrationExecutionContext
from app.orchestration.fallback import OrchestrationFallback
from app.orchestration.health import HealthContract
from app.orchestration.schemas import (
    ExecutedAction,
    ExecutionPlan,
    PlanStep,
    PlanTarget,
)
from app.services.agent_execution import ExecutionOrchestrator
from app.services.background_execution import BackgroundExecutionService
from app.services.integration_execution import IntegrationExecutionService
from app.services.tool_execution import ToolExecutionService
from app.tools.context import ToolExecutionContext
from app.tools.schemas import ToolExecutionRequest, ToolPermission


class OrchestrationExecutor(HealthContract):
    component_name = "executor"

    def __init__(
        self,
        *,
        tools: ToolExecutionService,
        integrations: IntegrationExecutionService,
        background: BackgroundExecutionService,
        agents: ExecutionOrchestrator,
        fallback: OrchestrationFallback | None = None,
    ) -> None:
        self.tools = tools
        self.integrations = integrations
        self.background = background
        self.agents = agents
        self.fallback = fallback or OrchestrationFallback()

    async def execute(
        self,
        plan: ExecutionPlan,
        context: OrchestrationExecutionContext,
    ) -> tuple[list[ExecutedAction], int]:
        actions = []
        retries = 0
        for step in plan.steps:
            if step.target is PlanTarget.RESPONSE:
                continue
            try:
                async def execute_step() -> dict[str, object]:
                    return await self._execute_step(step, context)

                output, step_retries = await self.fallback.retry(
                    execute_step
                )
                retries += step_retries
                actions.append(
                    ExecutedAction(
                        step_id=step.id,
                        target=step.target,
                        name=step.name,
                        success=True,
                        output=output,
                    )
                )
            except Exception as error:
                actions.append(
                    ExecutedAction(
                        step_id=step.id,
                        target=step.target,
                        name=step.name,
                        success=False,
                        output=self.fallback.graceful(error),
                        error=safe_failure_detail(
                            "Orchestration action failed", error
                        ),
                    )
                )
                if not self.fallback.policy.graceful_degradation:
                    raise
        return actions, retries

    async def _execute_step(
        self,
        step: PlanStep,
        context: OrchestrationExecutionContext,
    ) -> dict[str, object]:
        if step.target is PlanTarget.TOOL:
            tool_result = await self.tools.execute(
                ToolExecutionRequest(
                    tool_name=step.name, payload=step.payload
                ),
                self._tool_context(context),
            )
            return tool_result.model_dump(mode="json")
        if step.target is PlanTarget.INTEGRATION:
            if step.operation is None:
                raise ValueError("Integration operation is required")
            integration_result = await self.integrations.execute(
                IntegrationRequest(
                    connector=step.name,
                    operation=step.operation,
                    payload=step.payload,
                ),
                self._integration_context(context),
            )
            return integration_result.model_dump(mode="json")
        if step.target is PlanTarget.BACKGROUND:
            job = self.background.registry.get(step.name)
            background_result = await self.background.execute_job_now(
                BackgroundExecutionRequest(
                    job_name=step.name, payload=step.payload
                ),
                BackgroundExecutionContext(
                    user=context.user,
                    agent=context.agent,
                    conversation=context.conversation,
                    run=context.run,
                    job=job,
                    schedule=None,
                    user_permissions=frozenset(BackgroundPermission),
                    agent_permissions=frozenset(BackgroundPermission),
                    job_permissions=frozenset(BackgroundPermission),
                    tool_permissions=frozenset(BackgroundPermission),
                    integration_permissions=frozenset(
                        BackgroundPermission
                    ),
                    current_timestamp=context.current_timestamp,
                    trigger_source=BackgroundTriggerSource.MANUAL,
                    correlation_id=context.correlation_id,
                    timezone=context.timezone,
                    locale=context.locale,
                ),
            )
            return background_result.model_dump(mode="json")
        agent_result = await self.agents.execute_queued(
            context.run.id, context.user, provider_name=step.name
        )
        return {
            "run_id": str(agent_result.run_id),
            "provider": agent_result.provider_name,
            "delegated": True,
        }

    @staticmethod
    def _tool_context(
        context: OrchestrationExecutionContext,
    ) -> ToolExecutionContext:
        permissions = frozenset(
            permission
            for permission in ToolPermission
            if permission.value in context.permissions
            or "*" in context.permissions
        )
        return ToolExecutionContext(
            current_user=context.user,
            current_agent=context.agent,
            conversation=context.conversation,
            run=context.run,
            permissions=permissions,
            correlation_id=context.correlation_id,
            timezone=context.timezone,
            locale=context.locale,
            current_timestamp=context.current_timestamp,
        )

    @staticmethod
    def _integration_context(
        context: OrchestrationExecutionContext,
    ) -> IntegrationExecutionContext:
        permissions = frozenset(
            permission
            for permission in IntegrationPermission
            if permission.value in context.permissions
            or "*" in context.permissions
        )
        return IntegrationExecutionContext(
            user=context.user,
            agent=context.agent,
            conversation=context.conversation,
            run=context.run,
            user_permissions=permissions,
            agent_permissions=permissions,
            integration_permissions=permissions,
            correlation_id=context.correlation_id,
            timezone=context.timezone,
            locale=context.locale,
        )
