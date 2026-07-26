from collections.abc import AsyncIterator

from app.orchestration.health import HealthContract
from app.orchestration.schemas import (
    OrchestrationChunk,
    StreamEventType,
)


class OrchestrationStreamer(HealthContract):
    component_name = "streaming"

    async def stream(
        self,
        response: str,
        *,
        progress: tuple[str, ...] = (),
    ) -> AsyncIterator[OrchestrationChunk]:
        index = 0
        for update in progress:
            yield OrchestrationChunk(
                event_type=StreamEventType.PROGRESS,
                index=index,
                content=update,
            )
            index += 1
        words = response.split()
        for position, word in enumerate(words):
            final = position == len(words) - 1
            yield OrchestrationChunk(
                event_type=(
                    StreamEventType.FINAL
                    if final
                    else StreamEventType.CHUNK
                ),
                index=index,
                content=word + ("" if final else " "),
                final=final,
            )
            index += 1

    @staticmethod
    def tool_update(
        tool: str,
        status: str,
        *,
        index: int,
    ) -> OrchestrationChunk:
        return OrchestrationChunk(
            event_type=StreamEventType.TOOL_UPDATE,
            index=index,
            content=f"{tool}: {status}",
            metadata={"tool": tool, "status": status},
        )
