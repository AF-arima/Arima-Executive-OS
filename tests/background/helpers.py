from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from app.background.base import BackgroundJob
from app.background.context import BackgroundExecutionContext
from app.background.schemas import (
    BackgroundPermission,
    BackgroundTriggerSource,
)
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

FIXED_NOW = datetime(2026, 7, 26, 12, 0, tzinfo=timezone.utc)


async def make_context(
    session: AsyncSession,
    job: BackgroundJob,
    *,
    role_name: str = "administrator",
    permissions: frozenset[BackgroundPermission] = frozenset(
        BackgroundPermission
    ),
    run_status: AgentRunStatus = AgentRunStatus.RUNNING,
) -> BackgroundExecutionContext:
    user = User(
        email=f"background-{uuid4()}@example.com",
        hashed_password="hash",
        first_name="Background",
        last_name="Tester",
        is_active=True,
        is_verified=True,
    )
    role = Role(name=role_name, description=None)
    user.roles = [role]
    session.add_all([user, role])
    await session.flush()
    agent = AgentDefinition(
        slug=f"background-agent-{uuid4()}",
        name="Background Agent",
        description=None,
        system_instructions="Execute deterministic background work.",
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
        title="Background test",
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
        status=run_status,
        context_snapshot={},
        metadata_={},
    )
    session.add(run)
    await session.commit()
    return BackgroundExecutionContext(
        user=user,
        agent=agent,
        conversation=conversation,
        run=run,
        job=job,
        schedule=None,
        user_permissions=permissions,
        agent_permissions=permissions,
        job_permissions=permissions,
        tool_permissions=permissions,
        integration_permissions=permissions,
        current_timestamp=FIXED_NOW,
        trigger_source=BackgroundTriggerSource.MANUAL,
    )
