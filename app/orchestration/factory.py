from sqlalchemy.ext.asyncio import AsyncSession

from app.background.factory import BackgroundJobFactory
from app.core.config import get_settings
from app.integrations.factory import ConnectorFactory
from app.integrations.registry import ConnectorRegistry
from app.orchestration.approval import OrchestrationApprovalEngine
from app.orchestration.context import OrchestrationContextBuilder
from app.orchestration.cost import OrchestrationCostEngine
from app.orchestration.engine import OrchestrationEngine
from app.orchestration.executor import OrchestrationExecutor
from app.orchestration.fallback import OrchestrationFallback
from app.orchestration.memory import OrchestrationMemory
from app.orchestration.native_tools import NativeToolRegistry
from app.orchestration.optimizer import OrchestrationOptimizer
from app.orchestration.pipeline import OrchestrationPipeline
from app.orchestration.planner import OrchestrationPlanner
from app.orchestration.policy import OrchestrationPolicy
from app.orchestration.registry import (
    OrchestrationRegistry,
    StageHandler,
)
from app.orchestration.router import (
    AgentRouter,
    IntentEngine,
    ModelRouter,
    ProviderRouter,
)
from app.orchestration.schemas import OrchestrationStage
from app.orchestration.streaming import OrchestrationStreamer
from app.orchestration.telemetry import OrchestrationTelemetry, TelemetrySink
from app.providers.factory import ProviderFactory
from app.services.agent import MemoryService
from app.services.agent_execution import ExecutionOrchestrator
from app.services.background_execution import BackgroundExecutionService
from app.services.integration_execution import IntegrationExecutionService
from app.services.tool_execution import ToolExecutionService
from app.tools.factory import ToolFactory


class OrchestrationFactory:
    def __init__(
        self,
        session: AsyncSession,
        *,
        policy: OrchestrationPolicy | None = None,
        telemetry_sink: TelemetrySink | None = None,
    ) -> None:
        self.session = session
        self.policy = policy or OrchestrationPolicy()
        self.telemetry_sink = telemetry_sink

    def create(self) -> OrchestrationEngine:
        from app.integrations.microsoft_graph import build_native_registry

        providers = ProviderFactory().build_registry()
        tools_registry = ToolFactory(self.session).create_registry()
        # The legacy catalog is retained for deterministic development/test
        # plans. Production uses only identity-bound native providers below.
        settings = get_settings()
        integrations_registry = (
            ConnectorRegistry()
            if settings.environment == "production"
            or settings.microsoft_integration_enabled
            else ConnectorFactory().build_registry()
        )
        background_registry = BackgroundJobFactory().build_registry()
        fallback = OrchestrationFallback(self.policy)
        tools = ToolExecutionService(self.session, tools_registry)
        integrations = IntegrationExecutionService(
            self.session, integrations_registry
        )
        background = BackgroundExecutionService(
            self.session, registry=background_registry
        )
        agents = ExecutionOrchestrator.deterministic(self.session)
        executor = OrchestrationExecutor(
            tools=tools,
            integrations=integrations,
            background=background,
            agents=agents,
            fallback=fallback,
        )
        pipeline = OrchestrationPipeline(
            self.session,
            intent=IntentEngine(),
            agent_router=AgentRouter(),
            provider_router=ProviderRouter(providers),
            model_router=ModelRouter(),
            optimizer=OrchestrationOptimizer(),
            context_builder=OrchestrationContextBuilder(self.session),
            memory=OrchestrationMemory(MemoryService(self.session)),
            planner=OrchestrationPlanner(self.policy),
            approval=OrchestrationApprovalEngine(
                integrations_registry, background_registry
            ),
            executor=executor,
            streamer=OrchestrationStreamer(),
            fallback=fallback,
            cost=OrchestrationCostEngine(),
            telemetry=OrchestrationTelemetry(self.telemetry_sink),
            native_tool_registry=build_native_registry(self.session),
        )
        return OrchestrationEngine(pipeline)

    def registry(self, engine: OrchestrationEngine) -> OrchestrationRegistry:
        registry = OrchestrationRegistry()
        handlers: dict[OrchestrationStage, StageHandler] = {
            OrchestrationStage.USER_REQUEST: engine.execute,
            OrchestrationStage.INTENT_DETECTION: (
                engine.pipeline.intent.detect
            ),
            OrchestrationStage.AGENT_SELECTION: (
                engine.pipeline.agent_router.select
            ),
            OrchestrationStage.PROVIDER_SELECTION: (
                engine.pipeline.provider_router.select
            ),
            OrchestrationStage.MODEL_SELECTION: (
                engine.pipeline.model_router.select
            ),
            OrchestrationStage.CONTEXT_BUILDER: (
                engine.pipeline.context_builder.build
            ),
            OrchestrationStage.MEMORY_RETRIEVAL: (
                engine.pipeline.memory.optimise_context
            ),
            OrchestrationStage.PLANNING: engine.pipeline.planner.plan,
            OrchestrationStage.TOOL_SELECTION: (
                engine.pipeline.executor.execute
            ),
            OrchestrationStage.INTEGRATION_SELECTION: (
                engine.pipeline.executor.execute
            ),
            OrchestrationStage.APPROVAL_EVALUATION: (
                engine.pipeline.approval.evaluate
            ),
            OrchestrationStage.EXECUTION: engine.pipeline.executor.execute,
            OrchestrationStage.RESPONSE_ASSEMBLY: engine.pipeline.execute,
            OrchestrationStage.STREAMING: engine.pipeline.streamer.stream,
            OrchestrationStage.LOGGING: engine.pipeline.telemetry.record,
            OrchestrationStage.TELEMETRY: engine.pipeline.telemetry.record,
            OrchestrationStage.AUDIT: engine.pipeline.execute,
        }
        for stage, handler in handlers.items():
            registry.register(stage, handler)
        return registry
