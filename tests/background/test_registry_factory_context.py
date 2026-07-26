import asyncio
from datetime import datetime

import pytest

from app.background.clock import FixedClock
from app.background.context import BackgroundExecutionContext
from app.background.exceptions import (
    BackgroundConfigurationError,
    BackgroundPermissionDeniedError,
    BackgroundValidationError,
)
from app.background.factory import BackgroundJobFactory
from app.background.permissions import BackgroundPermissionValidator
from app.background.schemas import (
    BackgroundCapability,
    BackgroundJobCategory,
    BackgroundPermission,
)
from tests.background.helpers import FIXED_NOW, make_context
from tests.database.helpers import sqlite_session


def test_registry_factory_and_lookup_dimensions() -> None:
    registry = BackgroundJobFactory(
        clock=FixedClock(FIXED_NOW)
    ).build_registry()
    assert len(registry) == 12
    assert registry.get("executive_briefing").job_version() == "1.0.0"
    assert len(
        registry.find(category=BackgroundJobCategory.HEALTH)
    ) == 1
    assert len(
        registry.find(capability=BackgroundCapability.INTEGRATION)
    ) == 1
    assert len(
        registry.find(permission=BackgroundPermission.EXECUTE_AGENT)
    ) == 1
    with pytest.raises(BackgroundConfigurationError):
        registry.register(registry.get("executive_briefing"))
    assert asyncio.run(registry.health()).available


def test_context_validation_and_layered_permissions() -> None:
    async def scenario() -> None:
        async with sqlite_session() as session:
            job = BackgroundJobFactory(
                clock=FixedClock(FIXED_NOW)
            ).build_registry().get("project_status_review")
            context = await make_context(
                session,
                job,
                role_name="viewer",
                permissions=frozenset({BackgroundPermission.READ}),
            )
            with pytest.raises(BackgroundPermissionDeniedError):
                BackgroundPermissionValidator().require(context, None)
            with pytest.raises(BackgroundValidationError):
                BackgroundExecutionContext(
                    user=context.user,
                    agent=context.agent,
                    conversation=context.conversation,
                    run=context.run,
                    job=job,
                    schedule=None,
                    user_permissions=context.user_permissions,
                    agent_permissions=context.agent_permissions,
                    job_permissions=context.job_permissions,
                    tool_permissions=context.tool_permissions,
                    integration_permissions=context.integration_permissions,
                    current_timestamp=datetime(2026, 1, 1),
                    trigger_source=context.trigger_source,
                )

    asyncio.run(scenario())
