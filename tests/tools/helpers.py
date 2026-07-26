from __future__ import annotations

from uuid import uuid4

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
)
from app.tools.context import ToolExecutionContext
from app.tools.schemas import ToolPermission


async def make_context(
    session: AsyncSession,
    *,
    role_name: str = "administrator",
    permissions: frozenset[ToolPermission] = frozenset(ToolPermission),
) -> ToolExecutionContext:
    user = User(
        email=f"{uuid4()}@example.com",
        hashed_password="hash",
        first_name="Tool",
        last_name="Tester",
        is_active=True,
        is_verified=True,
    )
    role = Role(name=role_name, description=None)
    user.roles = [role]
    session.add_all([user, role])
    await session.flush()
    agent = AgentDefinition(
        slug=f"tool-agent-{uuid4()}",
        name="Tool Agent",
        description=None,
        system_instructions="Use internal tools.",
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
        title="Tool test",
        status=ConversationStatus.ACTIVE,
        priority=ConversationPriority.NORMAL,
        pinned=False,
        metadata_={},
    )
    session.add(conversation)
    await session.flush()
    run = AgentRun(
        conversation_id=conversation.id,
        agent_id=agent.id,
        triggered_by_id=user.id,
        status=AgentRunStatus.RUNNING,
        context_snapshot={},
        metadata_={},
    )
    session.add(run)
    await session.commit()
    return ToolExecutionContext(
        current_user=user,
        current_agent=agent,
        conversation=conversation,
        run=run,
        permissions=permissions,
    )
