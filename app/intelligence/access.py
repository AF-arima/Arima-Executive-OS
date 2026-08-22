from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    AIWorkspaceRun,
    AgentConversation,
    AgentDefinition,
    AgentRun,
    AgentStatus,
    User,
    WorkspaceAgentGrant,
    WorkspaceMembership,
)
from app.database.repositories.agent import AgentDefinitionRepository
from app.services.permissions import can_invoke_agents


class IntelligenceAccessError(PermissionError):
    pass


async def provision_default_agent_grant(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    granted_by_id: UUID,
) -> WorkspaceAgentGrant | None:
    """Attach the active platform default agent to a new workspace.

    The platform agent must already exist and be active; this helper never
    creates or globally enables an agent.  Returning ``None`` keeps account
    registration independent from optional agent-platform bootstrap.
    """
    agent = await AgentDefinitionRepository(session).get_active_default()
    if agent is None:
        return None
    grant = await session.scalar(
        select(WorkspaceAgentGrant).where(
            WorkspaceAgentGrant.workspace_id == workspace_id,
            WorkspaceAgentGrant.agent_id == agent.id,
        )
    )
    if grant is None:
        grant = WorkspaceAgentGrant(
            workspace_id=workspace_id,
            agent_id=agent.id,
            granted_by_id=granted_by_id,
        )
        session.add(grant)
    elif grant.revoked_at is not None:
        grant.revoked_at = None
        grant.granted_by_id = granted_by_id
    return grant


def require_workspace_affinity(
    conversation: AgentConversation,
    run: AgentRun,
    workspace_id: UUID,
) -> None:
    expected = str(workspace_id)
    if conversation.metadata_.get("workspace_id") != expected:
        raise IntelligenceAccessError("Conversation is not bound to this workspace")
    if run.context_snapshot.get("workspace_id") != expected:
        raise IntelligenceAccessError("AI run is not bound to this workspace")


async def require_workspace_membership(
    session: AsyncSession,
    user: User,
    workspace_id: UUID,
) -> WorkspaceMembership:
    if not user.is_active or not user.is_verified:
        raise IntelligenceAccessError("An active verified Arima user is required")
    membership = await session.scalar(
        select(WorkspaceMembership).where(
            WorkspaceMembership.workspace_id == workspace_id,
            WorkspaceMembership.user_id == user.id,
        )
    )
    if membership is None:
        raise IntelligenceAccessError("Workspace access is required")
    return membership


class AgentGrantService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def grant(
        self,
        *,
        workspace_id: UUID,
        agent_id: UUID,
        actor: User,
    ) -> WorkspaceAgentGrant:
        membership = await require_workspace_membership(
            self.session, actor, workspace_id
        )
        if membership.role != "owner":
            raise IntelligenceAccessError("Workspace owner authorization is required")
        agent = await self.session.get(AgentDefinition, agent_id)
        if agent is None or agent.status is not AgentStatus.ACTIVE:
            raise IntelligenceAccessError("An active agent is required")
        grant = await self.session.scalar(
            select(WorkspaceAgentGrant).where(
                WorkspaceAgentGrant.workspace_id == workspace_id,
                WorkspaceAgentGrant.agent_id == agent_id,
            )
        )
        if grant is None:
            grant = WorkspaceAgentGrant(
                workspace_id=workspace_id,
                agent_id=agent_id,
                granted_by_id=actor.id,
            )
            self.session.add(grant)
        else:
            grant.revoked_at = None
            grant.granted_by_id = actor.id
        await self.session.commit()
        return grant

    async def require(
        self, *, workspace_id: UUID, agent_id: UUID
    ) -> WorkspaceAgentGrant:
        grant = await self.session.scalar(
            select(WorkspaceAgentGrant).where(
                WorkspaceAgentGrant.workspace_id == workspace_id,
                WorkspaceAgentGrant.agent_id == agent_id,
                WorkspaceAgentGrant.revoked_at.is_(None),
            )
        )
        if grant is None:
            raise IntelligenceAccessError("Agent is not authorized for this workspace")
        return grant


class RunBindingService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def bind(
        self,
        *,
        workspace_id: UUID,
        run: AgentRun,
        actor: User,
        channel: str,
        correlation_id: UUID | None = None,
    ) -> AIWorkspaceRun:
        await require_workspace_membership(self.session, actor, workspace_id)
        if not can_invoke_agents(actor):
            raise IntelligenceAccessError("Agent invocation is not authorized")
        conversation = await self.session.get(AgentConversation, run.conversation_id)
        agent = await self.session.get(AgentDefinition, run.agent_id)
        if (
            conversation is None
            or conversation.owner_id != actor.id
            or run.triggered_by_id != actor.id
            or conversation.agent_id != run.agent_id
        ):
            raise IntelligenceAccessError("AI run ownership is invalid")
        require_workspace_affinity(conversation, run, workspace_id)
        if agent is None or agent.status is not AgentStatus.ACTIVE:
            raise IntelligenceAccessError("AI run agent is not active")
        await AgentGrantService(self.session).require(
            workspace_id=workspace_id, agent_id=run.agent_id
        )
        existing = await self.session.scalar(
            select(AIWorkspaceRun).where(AIWorkspaceRun.run_id == run.id)
        )
        if existing is not None:
            if existing.workspace_id != workspace_id or existing.user_id != actor.id:
                raise IntelligenceAccessError("AI run binding is immutable")
            return existing
        binding = AIWorkspaceRun(
            workspace_id=workspace_id,
            run_id=run.id,
            user_id=actor.id,
            channel=channel,
            correlation_id=correlation_id or uuid4(),
            created_at=datetime.now(UTC),
        )
        self.session.add(binding)
        await self.session.commit()
        return binding
