from decimal import Decimal
from typing import Protocol
from uuid import UUID

from app.execution.exceptions import ProviderUnavailable
from app.execution.types import (
    ProviderHealth,
    ProviderRequest,
    ProviderResponse,
    StructuredPrompt,
)


class ProviderAdapter(Protocol):
    @property
    def name(self) -> str: ...

    async def prepare(self, request: ProviderRequest) -> ProviderRequest: ...

    async def execute(self, request: ProviderRequest) -> ProviderResponse: ...

    async def cancel(self, run_id: UUID) -> None: ...

    def estimate_cost(
        self,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> Decimal: ...

    def estimate_tokens(self, prompt: StructuredPrompt) -> int: ...

    async def health(self) -> ProviderHealth: ...


class ProviderRegistry:
    def __init__(self, adapters: tuple[ProviderAdapter, ...] = ()) -> None:
        self._adapters = {adapter.name: adapter for adapter in adapters}

    def register(self, adapter: ProviderAdapter) -> None:
        self._adapters[adapter.name] = adapter

    def get(self, name: str) -> ProviderAdapter:
        adapter = self._adapters.get(name)
        if adapter is None:
            raise ProviderUnavailable(f"Provider adapter unavailable: {name}")
        return adapter


class MockProviderAdapter:
    name = "mock"

    def __init__(self) -> None:
        self.cancelled_runs: set[UUID] = set()

    async def prepare(self, request: ProviderRequest) -> ProviderRequest:
        return request

    async def execute(self, request: ProviderRequest) -> ProviderResponse:
        if request.run_id in self.cancelled_runs:
            raise ProviderUnavailable("Mock provider request was cancelled")
        history = request.prompt.conversation
        last_content = history[-1].content if history else "No user message"
        content = f"Mock response: {last_content}"
        return ProviderResponse(
            content=content,
            prompt_tokens=self.estimate_tokens(request.prompt),
            completion_tokens=max(1, len(content.split())),
            metadata={"adapter": self.name, "deterministic": True},
        )

    async def cancel(self, run_id: UUID) -> None:
        self.cancelled_runs.add(run_id)

    def estimate_cost(
        self,
        prompt_tokens: int,
        completion_tokens: int,
    ) -> Decimal:
        del prompt_tokens, completion_tokens
        return Decimal("0")

    def estimate_tokens(self, prompt: StructuredPrompt) -> int:
        return max(1, len(prompt.text().split()))

    async def health(self) -> ProviderHealth:
        return ProviderHealth(
            available=True,
            details={"adapter": self.name, "mode": "deterministic"},
        )
