from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.tools.base import ToolAdapter
from app.tools.exceptions import ToolConfigurationError
from app.tools.internal import INTERNAL_TOOL_TYPES
from app.tools.registry import ToolRegistry

ToolBuilder = Callable[[AsyncSession], ToolAdapter]


class ToolFactory:
    """DI-compatible factory for session-bound internal tools."""

    def __init__(
        self,
        session: AsyncSession,
        builders: Iterable[ToolBuilder] | None = None,
    ) -> None:
        self.session = session
        selected = builders or cast(
            tuple[ToolBuilder, ...], INTERNAL_TOOL_TYPES
        )
        self._builders: tuple[ToolBuilder, ...] = tuple(selected)
        if not self._builders:
            raise ToolConfigurationError("At least one tool is required")

    def create_all(self) -> tuple[ToolAdapter, ...]:
        return tuple(builder(self.session) for builder in self._builders)

    def create_registry(self) -> ToolRegistry:
        return ToolRegistry(self.create_all())

    def create(
        self, name: str, version: str | None = None
    ) -> ToolAdapter:
        return self.create_registry().get(name, version)
