import asyncio
from datetime import datetime

import pytest

from app.tools.context import ToolExecutionContext
from app.tools.exceptions import (
    ToolPermissionDeniedError,
    ToolValidationError,
)
from app.tools.permissions import ToolPermissionValidator
from app.tools.schemas import ToolPermission
from tests.database.helpers import sqlite_session
from tests.tools.helpers import make_context


def test_context_and_layered_permissions() -> None:
    async def scenario() -> None:
        async with sqlite_session() as session:
            context = await make_context(
                session,
                role_name="viewer",
                permissions=frozenset({ToolPermission.READ}),
            )
            validator = ToolPermissionValidator()
            assert validator.require(
                context, frozenset({ToolPermission.READ})
            ).allowed
            with pytest.raises(ToolPermissionDeniedError):
                validator.require(
                    context, frozenset({ToolPermission.WRITE})
                )
            with pytest.raises(ToolValidationError):
                ToolExecutionContext(
                    current_user=context.current_user,
                    current_agent=context.current_agent,
                    conversation=context.conversation,
                    run=context.run,
                    permissions=context.permissions,
                    current_timestamp=datetime(2026, 1, 1),
                )

    asyncio.run(scenario())
