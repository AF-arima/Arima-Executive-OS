from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    AIRetrievedContext,
    AIWorkspaceRun,
    AgentConversation,
    AgentMessage,
    AgentRun,
    AgentToolExecution,
    User,
)
from app.intelligence.access import (
    IntelligenceAccessError,
    require_workspace_affinity,
    require_workspace_membership,
)
from app.intelligence.schemas import AuditChain


class AuditChainService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def for_run(
        self, *, workspace_id: UUID, run_id: UUID, actor: User
    ) -> AuditChain:
        await require_workspace_membership(self.session, actor, workspace_id)
        binding = await self.session.scalar(
            select(AIWorkspaceRun).where(
                AIWorkspaceRun.workspace_id == workspace_id,
                AIWorkspaceRun.run_id == run_id,
                AIWorkspaceRun.user_id == actor.id,
            )
        )
        if binding is None:
            raise IntelligenceAccessError("AI run audit is unavailable")
        run = await self.session.get(AgentRun, run_id)
        if run is None or run.triggered_by_id != actor.id:
            raise IntelligenceAccessError("AI run audit is unavailable")
        conversation = await self.session.get(AgentConversation, run.conversation_id)
        if conversation is None or conversation.owner_id != actor.id:
            raise IntelligenceAccessError("AI run audit is unavailable")
        require_workspace_affinity(conversation, run, workspace_id)
        evidence_ids = tuple(
            (
                await self.session.scalars(
                    select(AIRetrievedContext.id)
                    .where(
                        AIRetrievedContext.workspace_id == workspace_id,
                        AIRetrievedContext.run_id == run_id,
                    )
                    .order_by(AIRetrievedContext.rank)
                )
            ).all()
        )
        tool_ids = tuple(
            (
                await self.session.scalars(
                    select(AgentToolExecution.id).where(
                        AgentToolExecution.run_id == run_id
                    )
                )
            ).all()
        )
        output = (
            await self.session.get(AgentMessage, run.output_message_id)
            if run.output_message_id is not None
            else None
        )
        if output is not None and output.conversation_id != conversation.id:
            raise IntelligenceAccessError("AI run output ownership is invalid")
        return AuditChain(
            user_id=actor.id,
            workspace_id=workspace_id,
            conversation_id=conversation.id,
            agent_id=run.agent_id,
            run_id=run.id,
            run_status=run.status.value,
            retrieved_context_ids=evidence_ids,
            tool_execution_ids=tool_ids,
            output_message_id=output.id if output is not None else None,
            resulting_action_ids=tool_ids,
        )
