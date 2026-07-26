from __future__ import annotations

from collections.abc import Iterable

from app.tools.base import ToolAdapter
from app.tools.exceptions import (
    ToolAlreadyRegisteredError,
    ToolNotFoundError,
)
from app.tools.schemas import ToolCapability, ToolCategory, ToolPermission


class ToolRegistry:
    def __init__(self, tools: Iterable[ToolAdapter] = ()) -> None:
        self._tools: dict[tuple[str, str], ToolAdapter] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: ToolAdapter) -> None:
        key = (tool.tool_name(), tool.tool_version())
        if key in self._tools:
            raise ToolAlreadyRegisteredError(
                f"Tool already registered: {key[0]}@{key[1]}"
            )
        self._tools[key] = tool

    def get(self, name: str, version: str | None = None) -> ToolAdapter:
        matches = [
            tool
            for (tool_name, tool_version), tool in self._tools.items()
            if tool_name == name
            and (version is None or tool_version == version)
        ]
        if not matches:
            suffix = f"@{version}" if version else ""
            raise ToolNotFoundError(f"Tool not registered: {name}{suffix}")
        return max(matches, key=lambda tool: tool.tool_version())

    def all(self) -> tuple[ToolAdapter, ...]:
        return tuple(self._tools.values())

    def by_category(self, category: ToolCategory) -> tuple[ToolAdapter, ...]:
        return tuple(
            tool
            for tool in self._tools.values()
            if tool.tool_category() is category
        )

    def by_permission(
        self, permission: ToolPermission
    ) -> tuple[ToolAdapter, ...]:
        return tuple(
            tool
            for tool in self._tools.values()
            if permission in tool.required_permissions()
        )

    def by_capability(
        self, capability: ToolCapability
    ) -> tuple[ToolAdapter, ...]:
        return tuple(
            tool
            for tool in self._tools.values()
            if capability in tool.capabilities()
        )

    def by_version(self, version: str) -> tuple[ToolAdapter, ...]:
        return tuple(
            tool
            for tool in self._tools.values()
            if tool.tool_version() == version
        )

    def __len__(self) -> int:
        return len(self._tools)
