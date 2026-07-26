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
from app.integrations.context import IntegrationExecutionContext
from app.integrations.schemas import IntegrationPermission


async def make_context(
    session: AsyncSession,
    *,
    role_name: str = "administrator",
    permissions: frozenset[IntegrationPermission] = frozenset(
        IntegrationPermission
    ),
) -> IntegrationExecutionContext:
    user = User(
        email=f"integration-{uuid4()}@example.com",
        hashed_password="hash",
        first_name="Integration",
        last_name="Tester",
        is_active=True,
        is_verified=True,
    )
    role = Role(name=role_name, description=None)
    user.roles = [role]
    session.add_all([user, role])
    await session.flush()
    agent = AgentDefinition(
        slug=f"integration-agent-{uuid4()}",
        name="Integration Agent",
        description=None,
        system_instructions="Use mock external connectors.",
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
        title="Integration test",
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
    return IntegrationExecutionContext(
        user=user,
        agent=agent,
        conversation=conversation,
        run=run,
        user_permissions=permissions,
        agent_permissions=permissions,
        integration_permissions=permissions,
    )
