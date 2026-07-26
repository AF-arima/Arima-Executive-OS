from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    AgentApprovalStatus,
    AgentMessage,
    AgentRunStatus,
    AuditAction,
    AuditEntity,
    MessageContentType,
    MessageRole,
    ToolExecutionStatus,
    User,
)
from app.database.repositories import (
    AgentApprovalRepository,
    AgentConversationRepository,
    AgentDefinitionRepository,
    AgentMessageRepository,
    AgentRunRepository,
    AgentToolExecutionRepository,
)
from app.execution.builders import ContextBuilder, PromptBuilder
from app.execution.estimators import (
    CostEstimator,
    MockTokenEstimator,
    TokenEstimator,
    ZeroPricingStrategy,
)
from app.execution.exceptions import (
    ApprovalRequired,
    ExecutionCancelled,
    ExecutionError,
    ExecutionTimeout,
    InvalidTransition,
    ProviderFailure,
    ProviderUnavailable,
    RetryExhausted,
)
from app.execution.policies import RetryExecutor, RetryPolicy, TimeoutPolicy
from app.execution.providers import ProviderRegistry
from app.execution.tools import ToolExecutionEngine
from app.execution.types import (
    ExecutionResult,
    ProviderRequest,
    ToolInvocation,
    ToolResult,
)
from app.schemas.agent import RunTransitionRequest
from app.services.agent import ApprovalService, RunService
from app.services.audit import record_audit
from app.services.exceptions import (
    ResourceConflictError,
    ResourceNotFoundError,
)
from app.services.notification import enqueue_agent_notification
from app.services.permissions import can_view_conversation

UTC = timezone.utc


class ExecutionEngine:
    def __init__(
        self,
        session: AsyncSession,
        *,
        providers: ProviderRegistry,
        tool_engine: ToolExecutionEngine,
        context_builder: ContextBuilder | None = None,
        prompt_builder: PromptBuilder | None = None,
        token_estimator: TokenEstimator | None = None,
        cost_estimator: CostEstimator | None = None,
        retry_policy: RetryPolicy | None = None,
        timeout_policy: TimeoutPolicy | None = None,
    ) -> None:
        self.session = session
        self.providers = providers
        self.tool_engine = tool_engine
        self.context_builder = context_builder or ContextBuilder(session)
        self.prompt_builder = prompt_builder or PromptBuilder()
        self.token_estimator = token_estimator or MockTokenEstimator()
        self.cost_estimator = cost_estimator or CostEstimator(
            ZeroPricingStrategy()
        )
        self.retry_policy = retry_policy or RetryPolicy(max_attempts=1)
        self.timeout_policy = timeout_policy or TimeoutPolicy(
            max_duration_ms=300_000
        )
        self.runs = AgentRunRepository(session)
        self.agents = AgentDefinitionRepository(session)
        self.conversations = AgentConversationRepository(session)
        self.messages = AgentMessageRepository(session)
        self.approvals = AgentApprovalRepository(session)
        self.executions = AgentToolExecutionRepository(session)

    async def execute(
        self,
        run_id: UUID,
        actor: User,
        *,
        provider_name: str,
        tool_invocations: tuple[ToolInvocation, ...] = (),
        _already_running: bool = False,
    ) -> ExecutionResult:
        run = await self.runs.get(run_id)
        if run is None:
            raise ResourceNotFoundError("Run not found")
        expected_status = (
            AgentRunStatus.RUNNING
            if _already_running
            else AgentRunStatus.QUEUED
        )
        if run.status is not expected_status:
            raise InvalidTransition(
                "Run is not ready for this execution operation"
            )
        conversation = await self.conversations.get(run.conversation_id)
        if conversation is None:
            raise ResourceNotFoundError("Conversation not found")
        if not can_view_conversation(actor, conversation):
            raise InvalidTransition("Actor cannot execute this run")

        try:
            if not _already_running:
                await RunService(self.session).start(run.id, actor)
            context = await self.context_builder.build(run.id, actor)
            agent = await self.agents.get(run.agent_id)
            if agent is None:
                raise ResourceNotFoundError("Agent not found")
            adapter = self.providers.get(provider_name)
            health = await adapter.health()
            if not health.available:
                raise ProviderUnavailable(
                    f"Provider adapter is unhealthy: {provider_name}"
                )
            await self._record_provider_selection(
                run.id,
                provider_name,
                actor,
            )

            tool_results: list[ToolResult] = []
            for invocation in tool_invocations:
                tool_results.append(
                    await self.tool_engine.execute(
                        run_id=run.id,
                        invocation=invocation,
                        context=context,
                        actor=actor,
                    )
                )
            prompt = self.prompt_builder.build(
                system_instructions=agent.system_instructions,
                context=context,
                tool_outputs=tuple(tool_results),
            )
            estimated_prompt_tokens = self.token_estimator.estimate_prompt(
                prompt
            )
            request = ProviderRequest(
                run_id=run.id,
                prompt=prompt,
                tool_results=tuple(tool_results),
                metadata={
                    "estimated_prompt_tokens": estimated_prompt_tokens,
                    "retry": self.retry_policy.backoff_metadata(1),
                    "timeout": self.timeout_policy.metadata(),
                },
            )
            prepared = await adapter.prepare(request)
            response = await RetryExecutor(self.retry_policy).run(
                lambda attempt: adapter.execute(
                    ProviderRequest(
                        run_id=prepared.run_id,
                        prompt=prepared.prompt,
                        tool_results=prepared.tool_results,
                        metadata={
                            **prepared.metadata,
                            "attempt": attempt,
                        },
                    )
                )
            )
            self.timeout_policy.ensure_within_limit(0)
            prompt_tokens = max(
                response.prompt_tokens,
                estimated_prompt_tokens,
            )
            completion_tokens = max(
                response.completion_tokens,
                self.token_estimator.estimate_text(response.content),
            )
            cost = self.cost_estimator.estimate(
                provider_name=provider_name,
                model_name=None,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
            output = await self._create_assistant_message(
                run_id=run.id,
                conversation_id=conversation.id,
                content=response.content,
                token_count=completion_tokens,
                actor=actor,
            )
            await RunService(self.session).complete(
                run.id,
                RunTransitionRequest(
                    status=AgentRunStatus.COMPLETED,
                    output_message_id=output.id,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    estimated_cost_gbp=cost.total_cost_gbp,
                ),
                actor,
            )
            return ExecutionResult(
                run_id=run.id,
                output_message_id=output.id,
                provider_name=provider_name,
                tool_results=tuple(tool_results),
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                estimated_cost_gbp=cost.total_cost_gbp,
            )
        except ApprovalRequired:
            current = await self.runs.get(run.id)
            if (
                current is not None
                and current.status is AgentRunStatus.RUNNING
            ):
                await RunService(self.session).wait_for_approval(
                    run.id,
                    actor,
                )
            raise
        except ExecutionCancelled:
            await self._cancel_if_active(run.id, actor)
            raise
        except (
            ExecutionTimeout,
            ProviderFailure,
            ProviderUnavailable,
            RetryExhausted,
        ) as error:
            await self._fail_if_active(run.id, actor, error)
            raise
        except ExecutionError as error:
            await self._fail_if_active(run.id, actor, error)
            raise
        except Exception as error:
            await self._fail_if_active(run.id, actor, error)
            raise

    async def resume(
        self,
        run_id: UUID,
        actor: User,
        *,
        provider_name: str,
        tool_invocations: tuple[ToolInvocation, ...],
    ) -> ExecutionResult:
        run = await self.runs.get(run_id)
        if run is None:
            raise ResourceNotFoundError("Run not found")
        if run.status is not AgentRunStatus.WAITING_FOR_APPROVAL:
            raise InvalidTransition(
                "Only approval-waiting runs can be resumed"
            )
        await RunService(self.session).resume(run.id, actor)
        return await self.execute(
            run.id,
            actor,
            provider_name=provider_name,
            tool_invocations=tool_invocations,
            _already_running=True,
        )

    async def cancel(self, run_id: UUID, actor: User) -> None:
        run = await self.runs.get(run_id)
        if run is None:
            raise ResourceNotFoundError("Run not found")
        if run.status in {
            AgentRunStatus.COMPLETED,
            AgentRunStatus.FAILED,
            AgentRunStatus.CANCELLED,
        }:
            raise InvalidTransition("Terminal runs cannot be cancelled")
        if run.model_provider:
            await self.providers.get(run.model_provider).cancel(run.id)
        approval_page = await self.approvals.list_scoped(
            run_id=run.id,
            status=AgentApprovalStatus.PENDING,
            limit=100,
            offset=0,
        )
        for approval in approval_page.items:
            resolved = await ApprovalService(self.session).cancel(
                approval.id,
                actor,
                decision_note="Execution cancelled",
            )
            enqueue_agent_notification(
                self.session,
                user_id=resolved.requested_by_id,
                entity_type="agent_approval",
                entity_id=resolved.id,
                title="Agent approval resolved",
                message="The approval was cancelled with its execution.",
            )
            await self.session.commit()
        execution_page = await self.executions.list_scoped(
            run_id=run.id,
            status=ToolExecutionStatus.PENDING,
            limit=100,
            offset=0,
        )
        for execution in execution_page.items:
            await self.tool_engine.cancel(execution.id, actor)
        try:
            await RunService(self.session).cancel(run.id, actor)
        except ResourceConflictError as error:
            raise InvalidTransition(str(error)) from error

    async def _record_provider_selection(
        self,
        run_id: UUID,
        provider_name: str,
        actor: User,
    ) -> None:
        run = await self.runs.get_for_update(run_id)
        if run is None:
            raise ResourceNotFoundError("Run not found")
        await self.runs.update(
            run,
            {"model_provider": provider_name, "model_name": None},
        )
        record_audit(
            self.session,
            actor_id=actor.id,
            action=AuditAction.UPDATE,
            entity=AuditEntity.AUTOMATION,
            entity_id=run.id,
        )
        await self.session.commit()

    async def _create_assistant_message(
        self,
        *,
        run_id: UUID,
        conversation_id: UUID,
        content: str,
        token_count: int,
        actor: User,
    ) -> AgentMessage:
        conversation = await self.conversations.get_for_update(
            conversation_id
        )
        if conversation is None:
            raise ResourceNotFoundError("Conversation not found")
        message = await self.messages.create_sequenced(
            conversation,
            {
                "run_id": run_id,
                "role": MessageRole.ASSISTANT,
                "content": content,
                "content_type": MessageContentType.TEXT,
                "token_count": token_count,
                "metadata": {"source": "execution_engine"},
                "created_by_id": actor.id,
            },
            created_at=datetime.now(UTC),
        )
        record_audit(
            self.session,
            actor_id=actor.id,
            action=AuditAction.CREATE,
            entity=AuditEntity.AUTOMATION,
            entity_id=message.id,
        )
        await self.session.commit()
        return message

    async def _fail_if_active(
        self,
        run_id: UUID,
        actor: User,
        error: Exception,
    ) -> None:
        run = await self.runs.get(run_id)
        if run is None or run.status is not AgentRunStatus.RUNNING:
            return
        await RunService(self.session).fail(
            run.id,
            RunTransitionRequest(
                status=AgentRunStatus.FAILED,
                failure_code=type(error).__name__,
                failure_message=str(error),
            ),
            actor,
        )

    async def _cancel_if_active(self, run_id: UUID, actor: User) -> None:
        run = await self.runs.get(run_id)
        if run is None or run.status not in {
            AgentRunStatus.QUEUED,
            AgentRunStatus.RUNNING,
            AgentRunStatus.WAITING_FOR_APPROVAL,
        }:
            return
        await self.cancel(run.id, actor)
