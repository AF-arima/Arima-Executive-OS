import asyncio
from dataclasses import replace
from uuid import uuid4

import pytest

from app.database.models import (
    AgentConversation,
    AgentMemoryScope,
    AgentMemoryType,
    AgentRun,
    AgentRunStatus,
    ConversationPriority,
    ConversationStatus,
    User,
)
from app.orchestration.context import OrchestrationContextBuilder
from app.orchestration.exceptions import OrchestrationConfigurationError
from app.orchestration.memory import OrchestrationMemory, RankedMemory
from app.schemas.agent import MemoryCreateRequest
from app.services.agent import MemoryService
from tests.database.helpers import sqlite_session
from tests.orchestration.helpers import make_context


def test_context_builder_merges_and_limits_context() -> None:
    async def scenario() -> None:
        async with sqlite_session() as session:
            context = await make_context(session)
            built = OrchestrationContextBuilder().build(
                context, memories=["decision: grow revenue"]
            )
            assert built.user_profile["id"] == str(context.user.id)
            assert built.agent_instructions
            assert built.memories == ["decision: grow revenue"]
            assert built.token_count <= built.token_limit

    asyncio.run(scenario())


def test_execution_context_rejects_foreign_conversation_and_run() -> None:
    async def scenario() -> None:
        async with sqlite_session() as session:
            context = await make_context(session)
            other_user = User(
                email=f"other-orchestration-{uuid4()}@example.com",
                hashed_password="hash",
                first_name="Other",
                last_name="User",
                is_active=True,
                is_verified=True,
            )
            session.add(other_user)
            await session.flush()

            foreign_conversation = AgentConversation(
                agent_id=context.agent.id,
                owner_id=other_user.id,
                title="Other user's conversation",
                status=ConversationStatus.ACTIVE,
                priority=ConversationPriority.NORMAL,
                pinned=False,
                metadata_={},
            )
            session.add(foreign_conversation)
            await session.flush()
            foreign_conversation_run = AgentRun(
                conversation_id=foreign_conversation.id,
                agent_id=context.agent.id,
                triggered_by_id=context.user.id,
                status=AgentRunStatus.RUNNING,
                context_snapshot={},
                metadata_={},
            )
            session.add(foreign_conversation_run)
            await session.flush()

            with pytest.raises(
                OrchestrationConfigurationError,
                match="Conversation does not belong to user",
            ):
                replace(
                    context,
                    conversation=foreign_conversation,
                    run=foreign_conversation_run,
                )

            foreign_run = AgentRun(
                conversation_id=context.conversation.id,
                agent_id=context.agent.id,
                triggered_by_id=other_user.id,
                status=AgentRunStatus.RUNNING,
                context_snapshot={},
                metadata_={},
            )
            session.add(foreign_run)
            await session.flush()

            with pytest.raises(
                OrchestrationConfigurationError,
                match="Run was not triggered by user",
            ):
                replace(context, run=foreign_run)

    asyncio.run(scenario())


def test_memory_search_ranking_compression_and_summary() -> None:
    async def scenario() -> None:
        async with sqlite_session() as session:
            context = await make_context(session)
            service = MemoryService(session)
            await service.create(
                MemoryCreateRequest(
                    conversation_id=context.conversation.id,
                    memory_type=AgentMemoryType.DECISION,
                    scope=AgentMemoryScope.CONVERSATION,
                    key="portfolio",
                    value="portfolio growth decision",
                    importance=5,
                ),
                context.user,
            )
            layer = OrchestrationMemory(service)
            ranked = await layer.search(context)
            assert ranked[0].key == "portfolio"
            assert layer.compress(ranked, 10)[0] == "portfolio "
            assert "portfolio:" in layer.summarise(ranked)
            assert RankedMemory("k", "v", 1).score == 1

    asyncio.run(scenario())
