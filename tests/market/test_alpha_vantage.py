import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx

from app.core.config import Settings
from app.market import (
    AlphaVantageProvider,
    CanonicalInstrument,
    MarketDataConfiguration,
    VerificationState,
)


def configuration(api_key: str | None = "alpha-secret") -> MarketDataConfiguration:
    return MarketDataConfiguration.from_settings(
        Settings(
            _env_file=None,
            market_data_provider="alpha_vantage",
            market_data_source="alpha_vantage",
            alpha_vantage_api_key=api_key,
        )
    )


def customer_display_configuration() -> MarketDataConfiguration:
    return MarketDataConfiguration.from_settings(
        Settings(
            _env_file=None,
            market_data_provider="alpha_vantage",
            market_data_source="alpha_vantage",
            alpha_vantage_api_key="alpha-secret",
            market_data_customer_display_entitled=True,
            market_data_real_time_entitled=True,
            market_data_usage_scope="customer_display",
            market_data_entitlement_reference="contract-approval-id",
        )
    )


def response(payload: object, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code,
        content=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )


def btc_payload() -> dict[str, object]:
    return {
        "Realtime Currency Exchange Rate": {
            "1. From_Currency Code": "BTC",
            "3. To_Currency Code": "USD",
            "5. Exchange Rate": "65000.12",
            "6. Last Refreshed": (datetime.now(UTC) - timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S"),
            "7. Time Zone": "UTC",
        }
    }


def gold_payload() -> dict[str, object]:
    return {
        "metal": "gold",
        "currency": "USD",
        "price": "2350.50",
        "timestamp": (datetime.now(UTC) - timedelta(seconds=1)).isoformat(),
    }


def test_missing_alpha_key_fails_closed_without_http() -> None:
    def unexpected(_: httpx.Request) -> httpx.Response:
        raise AssertionError("Alpha Vantage must not be called without a key")

    provider = AlphaVantageProvider(
        configuration(None), transport=httpx.MockTransport(unexpected)
    )
    verification = asyncio.run(provider.verify())
    assert verification.state is VerificationState.NOT_CONFIGURED
    assert verification.customer_prices_available is False


def test_alpha_provider_failure_is_unavailable() -> None:
    provider = AlphaVantageProvider(
        configuration(),
        transport=httpx.MockTransport(
            lambda _: response({"Note": "Thank you for using Alpha Vantage!"})
        ),
    )
    verification = asyncio.run(provider.verify())
    btc = next(
        item
        for item in verification.instruments
        if item.canonical is CanonicalInstrument.BTCUSD
    )
    assert btc.state is VerificationState.ERROR
    assert btc.reason == "alpha_vantage_provider_error"


def test_alpha_success_normalizes_btc_and_gold_evidence_without_secret() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["apikey"] == "alpha-secret"
        if request.url.params["function"] == "CURRENCY_EXCHANGE_RATE":
            return response(btc_payload())
        return response(gold_payload())

    async def scenario() -> None:
        provider = AlphaVantageProvider(
            customer_display_configuration(), transport=httpx.MockTransport(handler)
        )
        verification = await provider.verify()
        assert verification.customer_prices_available is True
        for instrument in (CanonicalInstrument.BTCUSD, CanonicalInstrument.XAUUSD):
            instrument_verification = next(
                item
                for item in verification.instruments
                if item.canonical is instrument
            )
            assert instrument_verification.available is True
            price, provenance = await provider.current_price(instrument)
            assert price > 0
            assert provenance.canonical is instrument
            assert provenance.provider.value == "alpha_vantage"
            assert "alpha-secret" not in provenance.model_dump_json()

    asyncio.run(scenario())


def test_alpha_provider_does_not_invent_customer_display_entitlement() -> None:
    provider = AlphaVantageProvider(
        configuration(),
        transport=httpx.MockTransport(
            lambda request: response(
                btc_payload()
                if request.url.params["function"] == "CURRENCY_EXCHANGE_RATE"
                else gold_payload()
            )
        ),
    )

    verification = asyncio.run(provider.verify())

    assert verification.state is VerificationState.VERIFIED_INTERNAL
    assert verification.customer_prices_available is False
    assert all(
        instrument.customer_display_entitled is False
        for instrument in verification.instruments
    )
