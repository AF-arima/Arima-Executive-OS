import asyncio

import pytest

from app.tools.exceptions import (
    ToolAlreadyRegisteredError,
    ToolNotFoundError,
)
from app.tools.factory import ToolFactory
from app.tools.schemas import (
    ToolCapability,
    ToolCategory,
    ToolPermission,
)
from tests.database.helpers import sqlite_session


def test_factory_registers_all_internal_tools_and_supports_lookups() -> None:
    async def scenario() -> None:
        async with sqlite_session() as session:
            registry = ToolFactory(session).create_registry()
            assert len(registry) == 26
            assert registry.get("project.search").tool_version() == "1.0.0"
            assert len(registry.by_category(ToolCategory.CRM)) == 4
            assert len(registry.by_permission(ToolPermission.WRITE)) == 1
            assert len(registry.by_version("1.0.0")) == 26
            assert len(
                registry.by_capability(ToolCapability.ANALYTICS)
            ) == 5
            with pytest.raises(ToolNotFoundError):
                registry.get("missing")
            with pytest.raises(ToolAlreadyRegisteredError):
                registry.register(registry.get("project.search"))

    asyncio.run(scenario())
