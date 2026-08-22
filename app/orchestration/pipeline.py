from __future__ import annotations

from datetime import datetime, timezone
from time import perf_counter
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database.models import (
    AgentRiskLevel,
    AgentToolDefinition,
    AgentToolExecution,
    AuditAction,
    AuditEntity,
    ToolExecutionMode,
    ToolExecutionStatus,
)
from app.orchestration.approval import OrchestrationApprovalEngine
from app.orchestration.context import (
    OrchestrationContextBuilder,
    OrchestrationExecutionContext,
)
from app.orchestration.cost import OrchestrationCostEngine
from app.orchestration.executor import OrchestrationExecutor
from app.orchestration.fallback import OrchestrationFallback
from app.voice.observability import observer_from_context
from app.orchestration.health import HealthContract
from app.orchestration.memory import OrchestrationMemory
from app.orchestration.market_response import (
    detect_response_language,
    market_response,
)
from app.orchestration.native_tools import (
    NativeExecutionContext,
    NativeToolLoop,
    NativeToolRegistry,
)
from app.orchestration.optimizer import OrchestrationOptimizer
from app.orchestration.planner import OrchestrationPlanner
from app.orchestration.provider_prompt import ProviderPromptBuilder
from app.orchestration.response_validation import ResponseValidator
from app.orchestration.router import (
    AgentRouter,
    IntentEngine,
    ModelRouter,
    ProviderRouter,
)
from app.orchestration.schemas import (
    AgentCandidate,
    ModelProfile,
    OrchestrationIntent,
    OrchestrationResult,
    ExecutedAction,
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
        provider_prompt: ProviderPromptBuilder | None = None,
        response_validator: ResponseValidator | None = None,
        native_tool_registry: NativeToolRegistry | None = None,
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
        self.provider_prompt = provider_prompt or ProviderPromptBuilder()
        self.response_validator = response_validator or ResponseValidator()
        self.native_tool_registry = native_tool_registry or NativeToolRegistry()

    async def execute(
        self, context: OrchestrationExecutionContext
    ) -> OrchestrationResult:
        started = perf_counter()
        intent = self.intent.detect(context.request)
        context.request.metadata["orchestration_intent"] = intent.value
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
        observer = observer_from_context(context)
        if observer is not None:
            observer.emit(
                "provider_selected",
                provider=provider.provider.value,
                outcome="success",
            )
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
        self._attach_live_tool_evidence(context, actions)
        built = self.context_builder.build(
            context,
            memories=memories,
            actions=actions,
            executive_state=executive_state,
        )
        provider_messages = (
            ProviderMessage(
                role=MessageRole.SYSTEM,
                content=self.provider_prompt.system_instructions,
            ),
            ProviderMessage(
                role=MessageRole.USER,
                content=self.provider_prompt.build(context, built),
            ),
        )
        if self._native_tools_enabled(context):
            native_context = context.request.metadata.get(
                "native_execution_context"
            )
            if not isinstance(native_context, NativeExecutionContext):
                native_context = await self._native_context(context)
            loop = NativeToolLoop(provider, self.native_tool_registry)
            (response, native_executions), provider_retries = (
                await self.fallback.retry(
                    lambda: loop.complete(
                        model=model,
                        messages=provider_messages,
                        max_output_tokens=context.request.max_output_tokens,
                        context=native_context,
                    ),
                    deadline=context.execution_deadline,
                    observer=observer.emit if observer is not None else None,
                    provider_name=provider.provider.value,
                )
            )
            await self._record_native_executions(context, native_executions)
        else:
            response, provider_retries = await self.fallback.retry(
                lambda: provider.complete(
                    CompletionRequest(
                        model=model,
                        messages=provider_messages,
                        max_output_tokens=context.request.max_output_tokens,
                        json_mode=context.request.require_json,
                    )
                ),
                deadline=context.execution_deadline,
                observer=observer.emit if observer is not None else None,
                provider_name=provider.provider.value,
            )
        retries += provider_retries
        if observer is not None:
            observer.emit(
                "response_received",
                provider=provider.provider.value,
                outcome="success",
            )
        validated = self.response_validator.validate(
            response.content,
            allowed_evidence_ids=frozenset(
                str(item)
                for item in context.request.metadata.get("evidence_ids", [])
            ),
        )
        deterministic_market_response = market_response(
            actions, language=detect_response_language(context.request.content)
        )
        if deterministic_market_response is not None:
            validated = self.response_validator.validate(
                deterministic_market_response,
                allowed_evidence_ids=frozenset(
                    str(item)
                    for item in context.request.metadata.get("evidence_ids", [])
                ),
            )
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
                validated.content,
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
            warnings=(
                warnings
                + (["Provider response was replaced by safety validation"]
                   if not validated.accepted else [])
            ),
            failures=failures,
            final_response=validated.content,
            chunks=chunks,
        )

    def _provider_capabilities(
        self,
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
        if self._native_tools_enabled(context):
            required.add(ProviderCapability.TOOLS)
        return frozenset(required)

    def _native_tools_enabled(
        self, context: OrchestrationExecutionContext
    ) -> bool:
        from app.core.config import get_settings

        metadata = context.request.metadata
        return bool(
            get_settings().microsoft_integration_enabled
            and
            self.native_tool_registry.declarations()
            and metadata.get("tenant_id")
            and metadata.get("workspace_id")
        )

    async def _native_context(
        self, context: OrchestrationExecutionContext
    ) -> NativeExecutionContext:
        from uuid import UUID

        from app.integrations.microsoft_graph import MicrosoftCredentialResolver

        metadata = context.request.metadata
        try:
            tenant_id = UUID(str(metadata["tenant_id"]))
            workspace_id = UUID(str(metadata["workspace_id"]))
        except (KeyError, TypeError, ValueError) as error:
            raise RuntimeError(
                "Native tool execution requires tenant and workspace identity"
            ) from error
        account_id = await MicrosoftCredentialResolver(self.session).account_id(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_id=context.user.id,
        )
        return NativeExecutionContext(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            actor_id=context.user.id,
            provider="microsoft",
            provider_account_id=account_id,
            agent=str(context.agent.id),
            approved_call_ids=frozenset(
                str(item)
                for item in metadata.get("approved_call_ids", [])
            ),
            destructive_guard_call_ids=frozenset(
                str(item)
                for item in metadata.get("destructive_guard_call_ids", [])
            ),
        )

    async def _record_native_executions(
        self, context: OrchestrationExecutionContext, executions
    ) -> None:
        """Persist safe execution metadata without tool arguments or content."""
        for execution in executions:
            tool = await self.session.scalar(
                select(AgentToolDefinition).where(
                    AgentToolDefinition.slug == f"native.{execution.canonical_name}"
                )
            )
            if tool is None:
                action = execution.provenance.authorization
                tool = AgentToolDefinition(
                    slug=f"native.{execution.canonical_name}",
                    name=execution.canonical_name,
                    description="Identity-bound native provider tool",
                    category=f"provider:{execution.provenance.provider}",
                    risk_level=(
                        AgentRiskLevel.HIGH
                        if "denied" in execution.provenance.status
                        else AgentRiskLevel.LOW
                    ),
                    execution_mode=ToolExecutionMode.PROVIDER,
                    requires_approval=action != "allowed",
                    is_enabled=True,
                    input_schema={"type": "object"},
                    output_schema={"type": "object"},
                )
                self.session.add(tool)
                await self.session.flush()
            self.session.add(
                AgentToolExecution(
                    run_id=context.run.id,
                    tool_id=tool.id,
                    status=(
                        ToolExecutionStatus.SUCCEEDED
                        if execution.provenance.status == "succeeded"
                        else ToolExecutionStatus.FAILED
                    ),
                    input_payload={
                        "call_id": execution.call_id,
                        "provider": execution.provenance.provider,
                        "provider_account_id": execution.provenance.provider_account_id,
                        "tenant_id": str(execution.provenance.tenant_id),
                        "workspace_id": str(execution.provenance.workspace_id),
                        "actor_id": str(execution.provenance.actor_id),
                    },
                    output_payload={
                        "authorization": execution.provenance.authorization,
                        "status": execution.provenance.status,
                        "timestamp": execution.provenance.timestamp.isoformat(),
                    },
                    error_code=(
                        None
                        if execution.provenance.status == "succeeded"
                        else execution.provenance.status
                    ),
                    error_message=None,
                    started_at=execution.provenance.timestamp,
                    completed_at=execution.provenance.timestamp,
                    duration_ms=round(execution.provenance.duration_ms),
                )
            )

    @staticmethod
    def _attach_live_tool_evidence(
        context: OrchestrationExecutionContext,
        actions: list[ExecutedAction],
    ) -> None:
        """Expose only explicitly shaped, verified tool evidence to providers."""
        evidence = list(context.request.metadata.get("provider_evidence", []))
        evidence_ids = list(context.request.metadata.get("evidence_ids", []))
        for action in actions:
            if action.name not in {
                "market.current_price",
                "weather.current",
                "runtime.current_date",
            }:
                continue
            if not action.success:
                evidence_id = str(uuid4())
                evidence.append({
                    "evidence_id": evidence_id,
                    "content": "The requested live data is unavailable from the verified server-side provider.",
                })
                evidence_ids.append(evidence_id)
                continue
            data = action.output.get("data")
            item = data.get("evidence") if isinstance(data, dict) else None
            if not isinstance(item, dict):
                continue
            evidence_id_value = item.get("evidence_id")
            content_value = item.get("content")
            if isinstance(evidence_id_value, str) and isinstance(content_value, str):
                evidence.append(
                    {"evidence_id": evidence_id_value, "content": content_value}
                )
                evidence_ids.append(evidence_id_value)
        context.request.metadata["provider_evidence"] = evidence
        context.request.metadata["evidence_ids"] = evidence_ids
