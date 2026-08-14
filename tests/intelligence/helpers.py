from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    AgentConversation,
    AgentDefinition,
    AgentRun,
    AgentRunStatus,
    AgentStatus,
    ConversationPriority,
    ConversationStatus,
    Role,
    User,
    Workspace,
    WorkspaceMembership,
)


@dataclass(frozen=True)
class IntelligenceContext:
    user: User
    workspace: Workspace
    agent: AgentDefinition
    conversation: AgentConversation
    run: AgentRun


async def make_intelligence_context(
    session: AsyncSession,
    *,
    role_name: str = "executive",
) -> IntelligenceContext:
    user = User(
        email=f"phase4-{uuid4()}@example.com",
        hashed_password="hash",
        first_name="Phase",
        last_name="Four",
        is_active=True,
        is_verified=True,
    )
    role = await session.scalar(select(Role).where(Role.name == role_name))
    if role is None:
        role = Role(name=role_name, description=None)
        session.add(role)
    user.roles = [role]
    session.add(user)
    await session.flush()
    workspace = Workspace(name="Executive workspace", owner_id=user.id)
    session.add(workspace)
    await session.flush()
    session.add(
        WorkspaceMembership(
            workspace_id=workspace.id,
            user_id=user.id,
            role="owner",
        )
    )
    agent = AgentDefinition(
        slug=f"phase4-agent-{uuid4()}",
        name="Executive Assistant",
        description=None,
        system_instructions="Provide governed executive assistance.",
        status=AgentStatus.ACTIVE,
        version=1,
        is_default=False,
        created_by_id=user.id,
    )
    session.add(agent)
    await session.flush()
    conversation = AgentConversation(
        agent_id=agent.id,
        owner_id=user.id,
        title="Phase 4",
        status=ConversationStatus.ACTIVE,
        priority=ConversationPriority.NORMAL,
        pinned=False,
        metadata_={"workspace_id": str(workspace.id)},
    )
    session.add(conversation)
    await session.flush()
    run = AgentRun(
        conversation_id=conversation.id,
        agent_id=agent.id,
        triggered_by_id=user.id,
        status=AgentRunStatus.QUEUED,
        context_snapshot={"workspace_id": str(workspace.id)},
        metadata_={},
    )
    session.add(run)
    await session.commit()
    return IntelligenceContext(user, workspace, agent, conversation, run)
