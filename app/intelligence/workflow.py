from __future__ import annotations

from datetime import UTC, datetime
import logging
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    AgentConversation,
    AgentDefinition,
    AgentMessage,
    AgentRunStatus,
    AuditAction,
    AuditEntity,
    MessageContentType,
    MessageRole,
    User,
)
from app.database.repositories import AgentMessageRepository
from app.intelligence.access import (
    AgentGrantService,
    RunBindingService,
    require_workspace_membership,
)
from app.intelligence.retrieval import TenantSafeRetrievalService
from app.intelligence.schemas import (
    RetrievalQuery,
    RetrievedKnowledge,
    WorkflowResult,
)
from app.orchestration.context import OrchestrationExecutionContext
from app.orchestration.engine import OrchestrationEngine
from app.orchestration.schemas import OrchestrationRequest
from app.schemas.agent import (
    ConversationCreateRequest,
    MessageCreateRequest,
    RunCreateRequest,
    RunTransitionRequest,
)
from app.services.agent import ConversationService, MessageService, RunService
from app.services.audit import record_audit
from app.services.permissions import user_roles

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "administrator": frozenset({"*"}),
    "executive": frozenset({"*"}),
    "manager": frozenset({"read", "write", "audit"}),
    "analyst": frozenset({"read", "write"}),
}
logger = logging.getLogger("arima.ai")


def _safe_failure_detail(error: Exception) -> str:
    return f"Executive workflow failed ({type(error).__name__})"


class ExecutiveWorkflowService:
    """Durable workspace workflow on top of the existing agent pipeline."""

    def __init__(
        self,
        session: AsyncSession,
        engine: OrchestrationEngine,
    ) -> None:
        self.session = session
        self.engine = engine

    async def execute_briefing(
        self,
        *,
        workspace_id: UUID,
        agent_id: UUID,
        actor: User,
        request: str,
        channel: str = "api",
        correlation_id: UUID | None = None,
    ) -> WorkflowResult:
        await require_workspace_membership(self.session, actor, workspace_id)
        await AgentGrantService(self.session).require(
            workspace_id=workspace_id, agent_id=agent_id
        )
        agent = await self.session.get(AgentDefinition, agent_id)
        if agent is None:
            raise PermissionError("Authorized agent is unavailable")
        conversation = await ConversationService(self.session).create(
            ConversationCreateRequest(
                agent_id=agent_id,
                title="Executive briefing",
                owner_id=actor.id,
                metadata={"channel": channel, "workspace_id": str(workspace_id)},
            ),
            actor,
        )
        input_message = await MessageService(self.session).create_user_message(
            MessageCreateRequest(
                conversation_id=conversation.id,
                role=MessageRole.USER,
                content=request,
                metadata={"channel": channel},
            ),
            actor,
        )
        run_service = RunService(self.session)
        run = await run_service.create(
            RunCreateRequest(
                conversation_id=conversation.id,
                input_message_id=input_message.id,
                context_snapshot={
                    "workspace_id": str(workspace_id),
                    "channel": channel,
                },
                metadata={"workflow": "executive_briefing"},
            ),
            actor,
        )
        binding = await RunBindingService(self.session).bind(
            workspace_id=workspace_id,
            run=run,
            actor=actor,
            channel=channel,
            correlation_id=correlation_id,
        )
        await run_service.start(run.id, actor)
        try:
            evidence = await TenantSafeRetrievalService(self.session).retrieve(
                workspace_id=workspace_id,
                run_id=run.id,
                actor=actor,
                query=RetrievalQuery(text=request),
            )
            context_text = self._context_text(request, evidence)
            permissions: set[str] = set()
            for role in user_roles(actor):
                permissions.update(ROLE_PERMISSIONS.get(role, ()))
            result = await self.engine.execute(
                OrchestrationExecutionContext(
                    user=actor,
                    agent=agent,
                    conversation=conversation,
                    run=run,
                    request=OrchestrationRequest(
                        content=context_text,
                        metadata={
                            "channel": channel,
                            "workspace_id": str(workspace_id),
                            "evidence_ids": [
                                str(item.evidence_id) for item in evidence
                            ],
                        },
                    ),
                    permissions=frozenset(permissions),
                    correlation_id=binding.correlation_id,
                )
            )
            output = await self._persist_output(
                conversation=conversation,
                run_id=run.id,
                actor=actor,
                response=result.final_response,
                evidence_ids=tuple(item.evidence_id for item in evidence),
            )
            run.model_provider = result.route.provider
            run.model_name = result.route.model
            await run_service.complete(
                run.id,
                RunTransitionRequest(
                    status=AgentRunStatus.COMPLETED,
                    output_message_id=output.id,
                    prompt_tokens=result.costs.input_tokens,
                    completion_tokens=result.costs.output_tokens,
                    estimated_cost_gbp=result.costs.total_cost_gbp,
                ),
                actor,
            )
        except Exception as error:
            logger.error(
                "ai_run_failed",
                extra={
                    "workspace_id": str(workspace_id),
                    "run_id": str(run.id),
                    "correlation_id": str(binding.correlation_id),
                    "error_type": type(error).__name__,
                },
            )
            await run_service.fail(
                run.id,
                RunTransitionRequest(
                    status=AgentRunStatus.FAILED,
                    failure_code="executive_workflow_failed",
                    failure_message=_safe_failure_detail(error),
                ),
                actor,
            )
            raise
        return WorkflowResult(
            conversation_id=conversation.id,
            run_id=run.id,
            output_message_id=output.id,
            response=result.final_response,
            evidence_ids=tuple(item.evidence_id for item in evidence),
        )

    async def _persist_output(
        self,
        *,
        conversation: AgentConversation,
        run_id: UUID,
        actor: User,
        response: str,
        evidence_ids: tuple[UUID, ...],
    ) -> AgentMessage:
        # The assistant output is system-authored and remains attributable to
        # the run; the initiating Arima user is recorded in the audit log.
        output = await AgentMessageRepository(self.session).create_sequenced(
            conversation,
            {
                "run_id": run_id,
                "role": MessageRole.ASSISTANT,
                "content": response,
                "content_type": MessageContentType.MARKDOWN,
                "metadata": {"evidence_ids": [str(item) for item in evidence_ids]},
                "created_by_id": None,
            },
            created_at=datetime.now(UTC),
        )
        record_audit(
            self.session,
            actor_id=actor.id,
            action=AuditAction.CREATE,
            entity=AuditEntity.AUTOMATION,
            entity_id=output.id,
        )
        await self.session.commit()
        return output

    @staticmethod
    def _context_text(request: str, evidence: tuple[RetrievedKnowledge, ...]) -> str:
        if not evidence:
            return request
        rendered = "\n\n".join(
            f"[evidence:{item.evidence_id}] {item.content}" for item in evidence
        )
        return (
            f"{request}\n\nApproved workspace context follows. Cite evidence IDs "
            f"for factual claims:\n{rendered}"
        )
