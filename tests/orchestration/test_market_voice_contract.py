import asyncio
import json
from uuid import uuid4

import pytest

from app.orchestration.context import BuiltOrchestrationContext
from app.orchestration.market_response import (
    detect_response_language,
    market_response,
)
from app.orchestration.provider_prompt import ProviderPromptBuilder
from app.orchestration.schemas import ExecutedAction, OrchestrationRequest, PlanTarget
from app.tools.internal.live_data import MarketPriceInput
from tests.database.helpers import sqlite_session
from tests.orchestration.helpers import make_context


def action(*, success: bool, data: dict[str, object] | None = None) -> ExecutedAction:
    return ExecutedAction(
        step_id=uuid4(),
        target=PlanTarget.TOOL,
        name="market.current_price",
        success=success,
        output={"data": data} if data is not None else {},
    )


def verified_quote() -> dict[str, object]:
    return {
        "price": "101234.50",
        "provider": "twelve_data",
        "source": "twelve_data_api",
        "verification_state": "verified_customer_display",
        "evidence": {
            "evidence_id": "btc-evidence",
            "content": "Verified current BTCUSD price.",
        },
    }


@pytest.mark.parametrize(
    ("text", "expected"),
    [("What is inflation?", "en"), ("تورم چیست؟", "fa")],
)
def test_language_detection_is_request_scoped(text: str, expected: str) -> None:
    assert detect_response_language(text) == expected


def test_language_switches_follow_each_current_request() -> None:
    assert [
        detect_response_language(text)
        for text in ("What is inflation?", "تورم چیست؟", "What is inflation?")
    ] == ["en", "fa", "en"]
    assert [
        detect_response_language(text)
        for text in ("تورم چیست؟", "What is inflation?")
    ] == ["fa", "en"]


@pytest.mark.parametrize(
    ("language", "expected"),
    [("en", "verified BTC/USD price"), ("fa", "قیمت تأییدشده")],
)
def test_verified_quote_is_localized_and_evidence_backed(language: str, expected: str) -> None:
    response = market_response([action(success=True, data=verified_quote())], language=language)
    assert response is not None
    assert expected in response
    assert "101234.50" in response
    assert "[evidence:btc-evidence]" in response


@pytest.mark.parametrize(
    ("language", "expected"),
    [("en", "A verified BTC price is currently unavailable."), ("fa", "قیمت تأییدشدهٔ بیت‌کوین")],
)
def test_unavailable_market_is_explicit_and_localized(language: str, expected: str) -> None:
    response = market_response([action(success=False)], language=language)
    assert response == expected or expected in response
    assert "125000" not in response
    assert "8420" not in response


def test_missing_market_provenance_fails_closed() -> None:
    incomplete = verified_quote()
    incomplete.pop("evidence")
    response = market_response([action(success=True, data=incomplete)], language="en")
    assert response == "A verified BTC price is currently unavailable."


def test_client_supplied_fake_price_is_not_a_market_quote() -> None:
    with pytest.raises(ValueError):
        MarketPriceInput.model_validate(
            {"instrument": "BTCUSD", "price": "125000"}
        )
    fake = verified_quote()
    fake["price"] = "125000"
    fake["verification_state"] = "unverified"
    response = market_response([action(success=True, data=fake)], language="en")
    assert response == "A verified BTC price is currently unavailable."


def test_provider_prompt_carries_current_request_language() -> None:
    async def scenario() -> None:
        async with sqlite_session() as session:
            context = await make_context(
                session, OrchestrationRequest(content="تورم چیست؟")
            )
            built = BuiltOrchestrationContext(
                system_prompt="",
                user_profile={},
                agent_instructions="",
                conversation=[],
                memories=[],
                tool_results=[],
                integration_results=[],
                background_results=[],
                token_count=0,
                token_limit=1,
            )
            payload = json.loads(ProviderPromptBuilder().build(context, built))
            assert payload["response_language"] == "fa"

    asyncio.run(scenario())


def test_current_news_prompt_requires_verified_news_evidence() -> None:
    async def scenario() -> None:
        async with sqlite_session() as session:
            request = OrchestrationRequest(
                content="What are the main financial news stories today?",
                metadata={"orchestration_intent": "current_news"},
            )
            context = await make_context(session, request)
            built = BuiltOrchestrationContext(
                system_prompt="",
                user_profile={},
                agent_instructions="",
                conversation=[],
                memories=[],
                tool_results=[],
                integration_results=[],
                background_results=[],
                token_count=0,
                token_limit=1,
            )
            payload = json.loads(ProviderPromptBuilder().build(context, built))
            assert "No verified live-news source is configured" in payload["current_news_policy"]

    asyncio.run(scenario())
