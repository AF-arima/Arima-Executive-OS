from __future__ import annotations

from datetime import datetime, timezone
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
from app.orchestration.context import OrchestrationExecutionContext
from app.orchestration.schemas import OrchestrationRequest

NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)


async def make_context(
    session: AsyncSession,
    request: OrchestrationRequest | None = None,
) -> OrchestrationExecutionContext:
    user = User(
        email=f"orchestration-{uuid4()}@example.com",
        hashed_password="hash",
        first_name="Arima",
        last_name="Executive",
        is_active=True,
        is_verified=True,
    )
    role = Role(name="administrator", description=None)
    user.roles = [role]
    session.add_all([user, role])
    await session.flush()
    agent = AgentDefinition(
        slug=f"orchestration-agent-{uuid4()}",
        name="Executive Agent",
        description=None,
        system_instructions="Act as a deterministic executive assistant.",
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
        title="Orchestration",
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
    return OrchestrationExecutionContext(
        user=user,
        agent=agent,
        conversation=conversation,
        run=run,
        request=request or OrchestrationRequest(content="Hello Arima"),
        permissions=frozenset({"*"}),
        current_timestamp=NOW,
    )
