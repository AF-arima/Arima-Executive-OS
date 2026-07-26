from __future__ import annotations

from dataclasses import dataclass

from app.database.models import AgentMemoryScope
from app.orchestration.context import OrchestrationExecutionContext
from app.orchestration.health import HealthContract
from app.services.agent import MemoryService


@dataclass(frozen=True, slots=True)
class RankedMemory:
    key: str
    value: str
    score: float


class OrchestrationMemory(HealthContract):
    component_name = "memory"

    def __init__(self, service: MemoryService | None = None) -> None:
        self.service = service

    async def search(
        self,
        context: OrchestrationExecutionContext,
        *,
        limit: int = 20,
    ) -> list[RankedMemory]:
        if self.service is None:
            return []
        page = await self.service.search_by_scope(
            scope=AgentMemoryScope.CONVERSATION,
            actor=context.user,
            conversation_id=context.conversation.id,
            limit=limit,
        )
        terms = set(context.request.content.lower().split())
        ranked = [
            RankedMemory(
                key=item.key,
                value=item.value,
                score=float(item.importance)
                + len(terms.intersection(item.value.lower().split())),
            )
            for item in page.items
        ]
        return sorted(ranked, key=lambda item: (-item.score, item.key))

    @staticmethod
    def compress(memories: list[RankedMemory], max_characters: int) -> list[str]:
        remaining = max_characters
        output = []
        for memory in memories:
            if remaining <= 0:
                break
            value = memory.value[:remaining]
            output.append(value)
            remaining -= len(value)
        return output

    @staticmethod
    def summarise(memories: list[RankedMemory]) -> str:
        return " | ".join(
            f"{memory.key}: {memory.value}" for memory in memories[:5]
        )

    async def optimise_context(
        self, context: OrchestrationExecutionContext
    ) -> list[str]:
        ranked = await self.search(context)
        return self.compress(
            ranked, max_characters=context.request.max_context_tokens * 4
        )
