import asyncio

import pytest

from app.integrations.connectors import SearchConnector
from app.integrations.context import IntegrationExecutionContext
from app.integrations.factory import ConnectorFactory
from app.integrations.schemas import (
    ConnectorHealthState,
    IntegrationRequest,
    ValidatedIntegrationRequest,
)
from app.services.integration_execution import IntegrationExecutionService
from tests.database.helpers import sqlite_session
from tests.integrations.helpers import make_context


class FailingSearchConnector(SearchConnector):
    def _response(
        self,
        request: ValidatedIntegrationRequest,
        context: IntegrationExecutionContext,
        *,
        dry_run: bool,
    ) -> dict[str, object]:
        raise RuntimeError("deterministic mock failure")


def test_every_connector_has_metadata_health_and_mock_execution() -> None:
    async def scenario() -> None:
        async with sqlite_session() as session:
            context = await make_context(session)
            registry = ConnectorFactory().build_registry()
            service = IntegrationExecutionService(session, registry)
            results = []
            for connector in registry.all():
                metadata = connector.metadata()
                assert metadata.mock is True
                assert metadata.operations
                initial_health = await connector.health()
                assert initial_health.available
                operation = metadata.operations[0]
                result = await service.execute(
                    IntegrationRequest(
                        connector=metadata.name,
                        operation=operation.name,
                        payload={"fixture": "deterministic"},
                    ),
                    context,
                )
                results.append(result)
                health = await connector.health()
                assert health.state is ConnectorHealthState.HEALTHY
                assert health.last_successful_execution is not None
            assert len(results) == 18
            assert all(result.success for result in results)

    asyncio.run(scenario())


def test_health_tracks_failed_connector_execution() -> None:
    async def scenario() -> None:
        async with sqlite_session() as session:
            context = await make_context(session)
            connector = FailingSearchConnector()
            request = connector.validate_request("web_search", {})
            with pytest.raises(RuntimeError):
                await connector.execute(request, context)
            health = await connector.health()
            assert health.last_failed_execution is not None

    asyncio.run(scenario())
