from typing import Any, Protocol
from uuid import UUID

from app.execution.exceptions import ToolFailure
from app.execution.types import ExecutionContext

MOCK_TOOL_SLUGS = (
    "projects.read",
    "tasks.read",
    "analytics.read",
    "crm.read",
    "outreach.read",
    "notifications.read",
    "memory.read",
    "memory.write",
    "approvals.request",
)


class ToolAdapter(Protocol):
    @property
    def slug(self) -> str: ...

    def validate(
        self,
        payload: dict[str, Any],
        context: ExecutionContext,
    ) -> None: ...

    async def execute(
        self,
        payload: dict[str, Any],
        context: ExecutionContext,
    ) -> dict[str, Any]: ...

    async def cancel(self, execution_id: UUID) -> None: ...

    def metadata(self) -> dict[str, Any]: ...


class ToolAdapterRegistry:
    def __init__(self, adapters: tuple[ToolAdapter, ...] = ()) -> None:
        self._adapters = {adapter.slug: adapter for adapter in adapters}

    def register(self, adapter: ToolAdapter) -> None:
        self._adapters[adapter.slug] = adapter

    def get(self, slug: str) -> ToolAdapter:
        adapter = self._adapters.get(slug)
        if adapter is None:
            raise ToolFailure(f"Tool adapter unavailable: {slug}")
        return adapter


class MockToolAdapter:
    def __init__(self, slug: str) -> None:
        if slug not in MOCK_TOOL_SLUGS:
            raise ValueError(f"Unsupported mock tool slug: {slug}")
        self._slug = slug
        self.cancelled_executions: set[UUID] = set()

    @property
    def slug(self) -> str:
        return self._slug

    def validate(
        self,
        payload: dict[str, Any],
        context: ExecutionContext,
    ) -> None:
        if context.run_id is None:
            raise ToolFailure("Execution context requires a run")
        if any(not isinstance(key, str) for key in payload):
            raise ToolFailure("Tool payload keys must be strings")

    async def execute(
        self,
        payload: dict[str, Any],
        context: ExecutionContext,
    ) -> dict[str, Any]:
        del context
        return {
            "tool": self.slug,
            "status": "mocked",
            "input": payload,
            "mutated": False,
        }

    async def cancel(self, execution_id: UUID) -> None:
        self.cancelled_executions.add(execution_id)

    def metadata(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "mode": "deterministic_mock",
            "external_integration": False,
        }


def mock_tool_adapters() -> tuple[ToolAdapter, ...]:
    return tuple(MockToolAdapter(slug) for slug in MOCK_TOOL_SLUGS)
