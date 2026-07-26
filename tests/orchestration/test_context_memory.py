import asyncio

from app.database.models import AgentMemoryScope, AgentMemoryType
from app.orchestration.context import OrchestrationContextBuilder
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
