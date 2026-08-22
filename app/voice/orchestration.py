from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    AgentMessage,
    AgentRunStatus,
    AuditAction,
    AuditEntity,
    MessageContentType,
    MessageRole,
)
from app.database.repositories import AgentMessageRepository
from app.orchestration.context import OrchestrationExecutionContext
from app.orchestration.engine import OrchestrationEngine
from app.orchestration.exceptions import OrchestrationApprovalRequired
from app.orchestration.schemas import OrchestrationResult
from app.schemas.agent import RunTransitionRequest
from app.services.agent import RunService
from app.services.audit import record_audit
from app.voice.observability import observer_from_context


class DurableVoiceOrchestration:
    """Preserve the Phase 4 run/output/audit lifecycle for Voice execution."""

    def __init__(
        self,
        database: AsyncSession,
        engine: OrchestrationEngine,
    ) -> None:
        self.database = database
        self.engine = engine

    async def execute(
        self,
        context: OrchestrationExecutionContext,
    ) -> OrchestrationResult:
        runs = RunService(self.database)
        try:
            result = await self.engine.execute(context)
            observer = observer_from_context(context)
            if observer is not None:
                observer.emit("persistence_started", outcome="started")
            output = await self._persist_output(context, result)
            if observer is not None:
                observer.emit("persistence_completed", outcome="success")
            context.run.model_provider = result.route.provider
            context.run.model_name = result.route.model
            await runs.complete(
                context.run.id,
                RunTransitionRequest(
                    status=AgentRunStatus.COMPLETED,
                    output_message_id=output.id,
                    prompt_tokens=result.costs.input_tokens,
                    completion_tokens=result.costs.output_tokens,
                    estimated_cost_gbp=result.costs.total_cost_gbp,
                ),
                context.user,
            )
            return result
        except OrchestrationApprovalRequired:
            await runs.wait_for_approval(context.run.id, context.user)
            raise
        except asyncio.CancelledError:
            await runs.fail(
                context.run.id,
                RunTransitionRequest(
                    status=AgentRunStatus.FAILED,
                    failure_code="voice_ai_execution_cancelled",
                    failure_message="Voice AI execution was cancelled",
                ),
                context.user,
            )
            raise
        except Exception as error:
            await runs.fail(
                context.run.id,
                RunTransitionRequest(
                    status=AgentRunStatus.FAILED,
                    failure_code="voice_ai_workflow_failed",
                    failure_message=(
                        f"Voice AI workflow failed ({type(error).__name__})"
                    ),
                ),
                context.user,
            )
            raise

    async def health(self):
        return await self.engine.health()

    async def _persist_output(
        self,
        context: OrchestrationExecutionContext,
        result: OrchestrationResult,
    ) -> AgentMessage:
        evidence_ids = context.request.metadata.get("evidence_ids", [])
        output = await AgentMessageRepository(
            self.database
        ).create_sequenced(
            context.conversation,
            {
                "run_id": context.run.id,
                "role": MessageRole.ASSISTANT,
                "content": result.final_response,
                "content_type": MessageContentType.MARKDOWN,
                "metadata": {"evidence_ids": evidence_ids},
                "created_by_id": None,
            },
            created_at=datetime.now(UTC),
        )
        record_audit(
            self.database,
            actor_id=context.user.id,
            action=AuditAction.CREATE,
            entity=AuditEntity.AUTOMATION,
            entity_id=output.id,
        )
        await self.database.commit()
        return output
