from __future__ import annotations

import json
from datetime import datetime, timezone
from time import perf_counter

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import AuditAction, AuditEntity
from app.orchestration.approval import OrchestrationApprovalEngine
from app.orchestration.context import (
    BuiltOrchestrationContext,
    OrchestrationContextBuilder,
    OrchestrationExecutionContext,
)
from app.orchestration.cost import OrchestrationCostEngine
from app.orchestration.executor import OrchestrationExecutor
from app.orchestration.fallback import OrchestrationFallback
from app.orchestration.health import HealthContract
from app.orchestration.memory import OrchestrationMemory
from app.orchestration.optimizer import OrchestrationOptimizer
from app.orchestration.planner import OrchestrationPlanner
from app.orchestration.router import (
    AgentRouter,
    IntentEngine,
    ModelRouter,
    ProviderRouter,
)
from app.orchestration.schemas import (
    AgentCandidate,
    ExecutedAction,
    ModelProfile,
    OrchestrationIntent,
    OrchestrationResult,
    RouteSelection,
    TelemetryRecord,
)
from app.orchestration.streaming import OrchestrationStreamer
from app.orchestration.telemetry import OrchestrationTelemetry
from app.providers.types import (
    CompletionRequest,
    MessageRole,
    ProviderCapability,
    ProviderMessage,
)
from app.services.audit import record_audit


class OrchestrationPipeline(HealthContract):
    component_name = "pipeline"

    def __init__(
        self,
        session: AsyncSession,
        *,
        intent: IntentEngine,
        agent_router: AgentRouter,
        provider_router: ProviderRouter,
        model_router: ModelRouter,
        optimizer: OrchestrationOptimizer,
        context_builder: OrchestrationContextBuilder,
        memory: OrchestrationMemory,
        planner: OrchestrationPlanner,
        approval: OrchestrationApprovalEngine,
        executor: OrchestrationExecutor,
        streamer: OrchestrationStreamer,
        fallback: OrchestrationFallback,
        cost: OrchestrationCostEngine,
        telemetry: OrchestrationTelemetry,
    ) -> None:
        self.session = session
        self.intent = intent
        self.agent_router = agent_router
        self.provider_router = provider_router
        self.model_router = model_router
        self.optimizer = optimizer
        self.context_builder = context_builder
        self.memory = memory
        self.planner = planner
        self.approval = approval
        self.executor = executor
        self.streamer = streamer
        self.fallback = fallback
        self.cost = cost
        self.telemetry = telemetry

    async def execute(
        self, context: OrchestrationExecutionContext
    ) -> OrchestrationResult:
        started = perf_counter()
        intent = self.intent.detect(context.request)
        candidates = context.agent_candidates or (
            AgentCandidate(
                agent_id=context.agent.id,
                name=context.agent.name,
                capabilities=frozenset(OrchestrationIntent),
                priority=100,
            ),
        )
        agent = self.agent_router.select(candidates, intent)
        profile = self.optimizer.model_profile(context.request, intent)
        required = self._provider_capabilities(context, profile)
        provider = await self.provider_router.select(required)
        model = self.model_router.select(provider, profile)
        route = RouteSelection(
            agent_id=agent.agent_id,
            provider=provider.provider.value,
            model=model,
            model_profile=profile,
            rationale=[
                f"intent={intent.value}",
                f"agent_priority={agent.priority}",
                f"profile={profile.value}",
            ],
        )
        memories = await self.memory.optimise_context(context)
        executive_state = await self.context_builder.resolve_executive_state(context)
        plan = self.planner.plan(intent, context.request.content)
        approved_steps = frozenset(
            str(item)
            for item in context.request.metadata.get("approved_steps", [])
        )
        approvals = self.approval.evaluate(
            plan, approved_steps=approved_steps
        )
        self.approval.require(approvals)
        actions, retries = await self.executor.execute(plan, context)
        built = self.context_builder.build(
            context,
            memories=memories,
            actions=actions,
            executive_state=executive_state,
        )
        response, provider_retries = await self.fallback.retry(
            lambda: provider.complete(
                CompletionRequest(
                    model=model,
                    messages=(
                        ProviderMessage(
                            role=MessageRole.SYSTEM,
                            content=built.system_prompt
                            + "\n"
                            + built.agent_instructions,
                        ),
                        ProviderMessage(
                            role=MessageRole.USER,
                            content=self._response_prompt(context, built, actions),
                        ),
                    ),
                    max_output_tokens=context.request.max_output_tokens,
                    json_mode=context.request.require_json,
                )
            )
        )
        retries += provider_retries
        tool_actions = [
            action
            for action in actions
            if action.target.value == "tool"
        ]
        integration_actions = [
            action
            for action in actions
            if action.target.value == "integration"
        ]
        job_actions = [
            action
            for action in actions
            if action.target.value == "background"
        ]
        cost = self.cost.estimate(
            provider,
            model=model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            tool_count=len(tool_actions),
            integration_count=len(integration_actions),
            budget_gbp=context.request.budget_gbp,
        )
        self.cost.require_budget(cost)
        chunks = []
        if context.request.stream:
            async for chunk in self.streamer.stream(
                response.content,
                progress=(
                    "intent_detected",
                    "plan_executed",
                    "response_assembled",
                ),
            ):
                chunks.append(chunk)
        latency = max(round((perf_counter() - started) * 1000, 3), 0)
        failures = [
            action.error
            for action in actions
            if not action.success and action.error is not None
        ]
        warnings = (
            ["Execution completed with graceful degradation"]
            if failures
            else []
        )
        telemetry = TelemetryRecord(
            correlation_id=context.correlation_id,
            latency_ms=latency,
            provider=provider.provider.value,
            agent_id=agent.agent_id,
            model=model,
            tool_count=len(tool_actions),
            integration_count=len(integration_actions),
            retries=retries,
            approval_count=len(approvals),
            failure_count=len(failures),
            success=not failures,
            timestamp=datetime.now(timezone.utc),
        )
        await self.telemetry.record(telemetry)
        record_audit(
            self.session,
            actor_id=context.user.id,
            action=AuditAction.COMPLETE,
            entity=AuditEntity.AUTOMATION,
            entity_id=context.correlation_id,
        )
        await self.session.commit()
        return OrchestrationResult(
            correlation_id=context.correlation_id,
            intent=intent,
            route=route,
            plan=plan,
            executed_tools=tool_actions,
            executed_integrations=integration_actions,
            executed_jobs=job_actions,
            approvals=approvals,
            costs=cost,
            latency_ms=latency,
            warnings=warnings,
            failures=failures,
            final_response=response.content,
            chunks=chunks,
        )

    @staticmethod
    def _provider_capabilities(
        context: OrchestrationExecutionContext,
        profile: ModelProfile,
    ) -> frozenset[ProviderCapability]:
        required: set[ProviderCapability] = set()
        if context.request.stream:
            required.add(ProviderCapability.STREAMING)
        if context.request.require_json:
            required.add(ProviderCapability.JSON_MODE)
        if context.request.has_images:
            required.add(ProviderCapability.VISION)
        if profile.value == "reasoning":
            required.add(ProviderCapability.REASONING)
        return frozenset(required)

    @staticmethod
    def _response_prompt(
        context: OrchestrationExecutionContext,
        built: BuiltOrchestrationContext,
        actions: list[ExecutedAction],
    ) -> str:
        sections = []
        if built.executive_state is not None:
            sections.append(
                "EXECUTIVE STATE\n"
                + json.dumps(
                    built.executive_state,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
        if built.memories:
            sections.append("MEMORY\n" + "\n".join(built.memories))
        summaries = [
            f"{action.name}={action.output}" for action in actions
        ]
        if summaries:
            sections.append("EXECUTION RESULTS\n" + "\n".join(summaries))
        sections.append("USER REQUEST\n" + context.request.content)
        return "\n\n".join(sections)
