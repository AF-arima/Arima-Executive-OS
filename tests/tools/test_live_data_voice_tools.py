import asyncio
import json
from unittest.mock import AsyncMock, patch
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest

from app.orchestration.context import BuiltOrchestrationContext
from app.orchestration.factory import OrchestrationFactory
from app.orchestration.pipeline import OrchestrationPipeline
from app.orchestration.planner import OrchestrationPlanner
from app.orchestration.provider_prompt import ProviderPromptBuilder
from app.orchestration.schemas import ExecutedAction, OrchestrationRequest, PlanTarget
from app.providers.types import CompletionRequest
from app.services.tool_execution import ToolExecutionService
from app.tools.exceptions import ToolPermissionDeniedError
from app.tools.factory import ToolFactory
from app.tools.internal.live_data import WeatherTool
from app.tools.logging import InMemoryToolExecutionLog
from app.tools.schemas import ToolExecutionRequest
from tests.database.helpers import sqlite_session
from tests.orchestration.helpers import make_context as make_orchestration_context
from tests.tools.helpers import make_context


@pytest.mark.parametrize(
    ("transcript", "tool_name", "payload"),
    [
        ("What’s the price of Bitcoin today?", "market.current_price", {"instrument": "BTCUSD"}),
        ("What’s the price of gold?", "market.current_price", {"instrument": "XAUUSD"}),
        ("What’s the weather like today?", "weather.current", {"location": None}),
        ("What’s the weather like in London today?", "weather.current", {"location": "London"}),
        ("What day is today and what is the date?", "runtime.current_date", {}),
    ],
)
def test_production_voice_phrases_select_live_data_tools(
    transcript: str, tool_name: str, payload: dict[str, str | None]
) -> None:
    from app.orchestration.schemas import OrchestrationIntent

    plan = OrchestrationPlanner().plan(OrchestrationIntent.GENERAL, transcript)
    assert plan.steps[0].name == tool_name
    assert plan.steps[0].payload == payload


def test_live_question_planning_and_evidence_for_gemini() -> None:
    async def scenario() -> None:
        async with sqlite_session() as session:
            context = await make_orchestration_context(
                session, OrchestrationRequest(content="What is Bitcoin's price?")
            )
            from app.orchestration.schemas import OrchestrationIntent
            plan = OrchestrationPlanner().plan(OrchestrationIntent.GENERAL, context.request.content)
            assert plan.steps[0].name == "market.current_price"
            action = ExecutedAction(
                step_id=uuid4(),
                target=PlanTarget.TOOL, name="market.current_price", success=True,
                output={"data": {"evidence": {"evidence_id": "live-btc", "content": "Verified current BTCUSD price is 100 USD."}}},
            )
            OrchestrationPipeline._attach_live_tool_evidence(context, [action])
            payload = json.loads(ProviderPromptBuilder().build(context, BuiltOrchestrationContext(
                system_prompt="", user_profile={}, agent_instructions="", conversation=[], memories=[], tool_results=[], integration_results=[], background_results=[], token_count=0, token_limit=1,
            )))
            assert payload["evidence"] == [{"evidence_id": "live-btc", "content": "Verified current BTCUSD price is 100 USD."}]
            failed = ExecutedAction(
                step_id=uuid4(), target=PlanTarget.TOOL, name="market.current_price",
                success=False, error="provider failure",
            )
            OrchestrationPipeline._attach_live_tool_evidence(context, [failed])
            assert "unavailable" in context.request.metadata["provider_evidence"][-1]["content"]

    asyncio.run(scenario())


def test_runtime_date_is_server_evidence_and_audited() -> None:
    async def scenario() -> None:
        async with sqlite_session() as session:
            context = await make_context(session)
            result = await ToolExecutionService(
                session, ToolFactory(session).create_registry()
            ).execute(
                ToolExecutionRequest(tool_name="runtime.current_date", payload={}),
                context,
            )
            assert result.success is True
            local_now = context.current_timestamp.astimezone(
                ZoneInfo(context.timezone)
            )
            assert result.data["date"] == local_now.date().isoformat()
            assert result.data["weekday"] == local_now.strftime("%A")
            assert "server runtime date" in result.data["evidence"]["content"]

    asyncio.run(scenario())


def test_btc_tool_failure_reaches_gemini_as_unavailable_evidence() -> None:
    async def scenario() -> None:
        async with sqlite_session() as session:
            engine = OrchestrationFactory(session).create()
            provider = engine.pipeline.provider_router.registry.list()[0]
            original = provider.complete
            captured = AsyncMock(wraps=original)
            context = await make_orchestration_context(
                session,
                OrchestrationRequest(
                    content="What’s the price of Bitcoin today?"
                ),
            )
            with patch.object(provider, "complete", captured):
                result = await engine.execute(context)
            assert result.executed_tools[0].name == "market.current_price"
            assert result.executed_tools[0].success is False
            request = captured.await_args.args[0]
            assert isinstance(request, CompletionRequest)
            assert "live data is unavailable" in request.messages[1].content

    asyncio.run(scenario())


def test_gold_and_missing_weather_location_are_safe_and_audited() -> None:
    async def scenario() -> None:
        async with sqlite_session() as session:
            context = await make_context(session)
            assert OrchestrationPlanner()._live_data_step("gold price").payload == {"instrument": "XAUUSD"}
            log = InMemoryToolExecutionLog()
            result = await ToolExecutionService(session, ToolFactory(session).create_registry(), log_sink=log).execute(
                ToolExecutionRequest(tool_name="weather.current", payload={}), context
            )
            assert result.success and result.data["location_required"] is True
            assert "do not guess" in result.data["evidence"]["content"]
            assert log.records()[0].tool == "weather.current"

    asyncio.run(scenario())


def test_weather_with_location_returns_provider_evidence() -> None:
    class Response:
        def __init__(self, body): self.body = body
        def raise_for_status(self): return None
        def json(self): return self.body
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *_): return None
        async def get(self, url, **_):
            return Response({"results": [{"name": "London", "country": "United Kingdom", "latitude": 51.5, "longitude": -0.1}]} if "geocoding" in url else {"current": {"time": "2026-08-17T12:00", "temperature_2m": 20, "apparent_temperature": 19, "weather_code": 1, "wind_speed_10m": 8}})
    async def scenario() -> None:
        async with sqlite_session() as session:
            context = await make_context(session)
            with patch("app.tools.internal.live_data.httpx.AsyncClient", return_value=Client()):
                result = await WeatherTool(session).execute(WeatherTool.input_model(location="London"), context)
            assert result["provider"] == "open_meteo"
            assert result["location"] == "London, United Kingdom"
            assert "observed at" in result["evidence"]["content"]
    asyncio.run(scenario())


def test_live_tool_provider_failure_and_unauthorised_access_fail_closed() -> None:
    async def scenario() -> None:
        async with sqlite_session() as session:
            context = await make_context(session)
            service = ToolExecutionService(session, ToolFactory(session).create_registry())
            failed = await service.execute(ToolExecutionRequest(tool_name="market.current_price", payload={"instrument": "BTCUSD"}), context)
            assert failed.success is False and "credentials" not in (failed.failure or "")
            denied = await make_context(session, role_name="viewer", permissions=frozenset())
            with pytest.raises(ToolPermissionDeniedError):
                await service.execute(ToolExecutionRequest(tool_name="weather.current", payload={}), denied)
    asyncio.run(scenario())
