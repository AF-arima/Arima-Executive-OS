import asyncio
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.market import (
    CanonicalInstrument,
    EntitlementState,
    FreshnessState,
    MarketDataConfiguration,
    MarketDataProvenance,
    MarketDataProviderName,
    MarketDataSource,
    TwelveDataProvider,
    VerificationState,
)


def configuration(*, api_key: str | None = "server-secret") -> MarketDataConfiguration:
    return MarketDataConfiguration.from_settings(
        Settings(_env_file=None, twelve_data_api_key=api_key)
    )


def commercial_configuration() -> MarketDataConfiguration:
    return MarketDataConfiguration.from_settings(
        Settings(
            _env_file=None,
            twelve_data_api_key="server-secret",
            market_data_account_plan="enterprise",
            market_data_usage_scope="redistribution",
            market_data_customer_display_entitled=True,
            market_data_redistribution_entitled=True,
            market_data_real_time_entitled=True,
            market_data_entitlement_reference="contract-approval-id",
        )
    )


def response(status_code: int, payload: object) -> httpx.Response:
    return httpx.Response(
        status_code,
        content=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )


def usage_payload(plan: str = "Basic") -> dict[str, object]:
    return {
        "status": "ok",
        "timestamp": "2026-08-13T12:00:00Z",
        "current_usage": 1,
        "plan_limit": 8,
        "plan_category": plan,
    }


def test_missing_credentials_fail_closed_without_http_request() -> None:
    def unexpected_request(_: httpx.Request) -> httpx.Response:
        raise AssertionError("provider must not be called without credentials")

    provider = TwelveDataProvider(
        configuration(api_key=None),
        transport=httpx.MockTransport(unexpected_request),
    )
    health = provider.configuration_health()
    verification = asyncio.run(provider.verify())

    assert health.configured is False
    assert health.issues == ("provider_credentials_missing",)
    assert verification.authenticated is False
    assert verification.state is VerificationState.NOT_CONFIGURED
    assert verification.customer_prices_available is False


def test_authentication_failure_is_unavailable() -> None:
    provider = TwelveDataProvider(
        configuration(),
        transport=httpx.MockTransport(
            lambda _: response(401, {"status": "error"})
        ),
    )

    verification = asyncio.run(provider.verify())

    assert verification.reason == "provider_authentication_failed"
    assert verification.state is VerificationState.AUTHENTICATION_FAILED
    assert verification.authenticated is False
    assert verification.customer_prices_available is False


def test_non_price_verification_keeps_entitlement_fail_closed() -> None:
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        assert request.headers["Authorization"] == "apikey server-secret"
        if request.url.path == "/api_usage":
            return response(200, usage_payload())
        symbol = request.url.params["symbol"]
        metadata = {
            "XAU/USD": (
                "Gold Spot / US Dollar",
                "Commodity",
                "Commodity",
            ),
            "BTC/USD": (
                "Bitcoin to US Dollar",
                "Digital Currency",
                "Coinbase Pro",
            ),
            "SPX": ("S&P 500 Index", "Index", "CBOE"),
        }
        instrument_name, instrument_type, exchange = metadata[symbol]
        return response(
            200,
            {
                "status": "ok",
                "data": [
                    {
                        "symbol": symbol,
                        "instrument_name": instrument_name,
                        "instrument_type": instrument_type,
                        "exchange": exchange,
                        "currency": "USD",
                        "access": {
                            "global": "Basic",
                            "plan": "Basic",
                            "plan_business": "Basic",
                        },
                    }
                ],
            },
        )

    provider = TwelveDataProvider(
        configuration(),
        transport=httpx.MockTransport(handler),
    )
    verification = asyncio.run(provider.verify())

    assert verification.authenticated is True
    assert verification.provider_verified is True
    assert all(item.mapping_verified for item in verification.instruments)
    assert all(
        item.entitlement_state
        is EntitlementState.INTERNAL_NON_DISPLAY_ONLY
        for item in verification.instruments
    )
    assert all(
        not item.customer_display_entitled
        for item in verification.instruments
    )
    assert verification.customer_prices_available is False
    assert requested_paths == [
        "/api_usage",
        "/symbol_search",
        "/symbol_search",
        "/symbol_search",
    ]
    assert not {"/price", "/quote", "/time_series"} & set(requested_paths)


def test_written_commercial_rights_can_reach_customer_display_state() -> None:
    configuration_value = commercial_configuration()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api_usage":
            return response(200, usage_payload("Enterprise"))
        symbol = request.url.params["symbol"]
        mapping = next(
            item
            for item in configuration_value.mappings
            if item.provider_symbol == symbol
        )
        if request.url.path == "/quote":
            return response(
                200,
                {
                    "symbol": mapping.provider_symbol,
                    "name": mapping.expected_name,
                    "exchange": mapping.exchange,
                    "currency": mapping.currency,
                    "timestamp": int(datetime.now(timezone.utc).timestamp()),
                    "close": "discarded-value",
                },
            )
        return response(
            200,
            {
                "status": "ok",
                "data": [
                    {
                        "symbol": mapping.provider_symbol,
                        "instrument_name": mapping.expected_name,
                        "instrument_type": mapping.instrument_type.value,
                        "exchange": mapping.exchange,
                        "currency": mapping.currency,
                        "access": {
                            "global": "Basic",
                            "plan": "Basic",
                            "plan_business": "Basic",
                        },
                    }
                ],
            },
        )

    verification = asyncio.run(
        TwelveDataProvider(
            configuration_value,
            transport=httpx.MockTransport(handler),
        ).verify()
    )

    assert (
        verification.state
        is VerificationState.VERIFIED_CUSTOMER_DISPLAY
    )
    assert verification.customer_prices_available is True
    assert all(
        item.redistribution_entitled for item in verification.instruments
    )
    assert all(
        item.freshness is FreshnessState.FRESH
        for item in verification.instruments
    )
    assert "discarded-value" not in verification.model_dump_json()


def test_provider_account_plan_mismatch_fails_before_symbol_requests() -> None:
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        return response(200, usage_payload("Grow"))

    verification = asyncio.run(
        TwelveDataProvider(
            configuration(), transport=httpx.MockTransport(handler)
        ).verify()
    )

    assert verification.state is VerificationState.ENTITLEMENT_UNVERIFIED
    assert verification.account_plan_verified is False
    assert verification.provider_account_plan == "Grow"
    assert requested_paths == ["/api_usage"]


def test_spy_etf_cannot_verify_the_spx_mapping() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api_usage":
            return response(200, usage_payload())
        symbol = request.url.params["symbol"]
        if symbol != "SPX":
            mapping = next(
                item
                for item in configuration().mappings
                if item.provider_symbol == symbol
            )
            return response(
                200,
                {
                    "status": "ok",
                    "data": [
                        {
                            "symbol": mapping.provider_symbol,
                            "instrument_name": mapping.expected_name,
                            "instrument_type": mapping.instrument_type.value,
                            "exchange": mapping.exchange,
                            "currency": mapping.currency,
                            "access": {
                                "global": "Basic",
                                "plan": "Basic",
                                "plan_business": "Basic",
                            },
                        }
                    ],
                },
            )
        return response(
            200,
            {
                "status": "ok",
                "data": [
                    {
                        "symbol": "SPY",
                        "instrument_name": "SPDR S&P 500 ETF Trust",
                        "instrument_type": "ETF",
                        "exchange": "NYSE",
                        "currency": "USD",
                    }
                ],
            },
        )

    verification = asyncio.run(
        TwelveDataProvider(
            configuration(),
            transport=httpx.MockTransport(handler),
        ).verify()
    )

    spx = next(
        item
        for item in verification.instruments
        if item.canonical is CanonicalInstrument.SPX
    )
    assert spx.mapping_verified is False
    assert verification.customer_prices_available is False


def test_unverified_symbol_mapping_fails_entire_provider_closed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api_usage":
            return response(200, usage_payload())
        return response(200, {"status": "ok", "data": []})

    verification = asyncio.run(
        TwelveDataProvider(
            configuration(),
            transport=httpx.MockTransport(handler),
        ).verify()
    )

    assert verification.provider_verified is False
    assert verification.reason == "provider_identity_unverified"
    assert verification.state is VerificationState.SYMBOL_UNVERIFIED
    assert verification.customer_prices_available is False


def test_rate_limit_and_entitlement_failures_are_unavailable() -> None:
    for status_code, expected_reason in (
        (403, "provider_authentication_failed"),
        (429, "provider_rate_limited"),
    ):
        provider = TwelveDataProvider(
            configuration(),
            transport=httpx.MockTransport(
                lambda _, code=status_code: response(
                    code, {"status": "error"}
                )
            ),
        )
        verification = asyncio.run(provider.verify())
        assert verification.reason == expected_reason
        assert verification.customer_prices_available is False


def test_timeout_fails_closed() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out", request=request)

    verification = asyncio.run(
        TwelveDataProvider(
            configuration(),
            transport=httpx.MockTransport(timeout),
        ).verify()
    )

    assert verification.reason == "provider_unavailable"
    assert verification.state is VerificationState.PROVIDER_UNAVAILABLE
    assert verification.customer_prices_available is False


def test_connection_failure_is_provider_unavailable() -> None:
    def failure(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection failed", request=request)

    verification = asyncio.run(
        TwelveDataProvider(
            configuration(),
            transport=httpx.MockTransport(failure),
        ).verify()
    )

    assert verification.state is VerificationState.PROVIDER_UNAVAILABLE
    assert verification.customer_prices_available is False


def test_malformed_provider_response_is_error_and_fail_closed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api_usage":
            return response(200, usage_payload())
        return response(200, {"status": "ok", "data": "not-a-list"})

    verification = asyncio.run(
        TwelveDataProvider(
            configuration(),
            transport=httpx.MockTransport(handler),
        ).verify()
    )

    assert verification.state is VerificationState.ERROR
    assert verification.customer_prices_available is False


@pytest.mark.parametrize(
    "usage",
    [
        {
            "status": "unexpected",
            "timestamp": "2026-08-13T12:00:00Z",
            "current_usage": 1,
            "plan_limit": 8,
            "plan_category": "Basic",
        },
        {"status": "ok", "plan_category": "Basic"},
    ],
)
def test_unverified_usage_metadata_fails_closed(
    usage: dict[str, object],
) -> None:
    verification = asyncio.run(
        TwelveDataProvider(
            configuration(),
            transport=httpx.MockTransport(lambda _: response(200, usage)),
        ).verify()
    )

    assert verification.state is VerificationState.ERROR
    assert verification.authenticated is False
    assert verification.customer_prices_available is False


def test_missing_global_catalog_access_fails_closed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api_usage":
            return response(200, usage_payload())
        mapping = next(
            item
            for item in configuration().mappings
            if item.provider_symbol == request.url.params["symbol"]
        )
        return response(
            200,
            {
                "status": "ok",
                "data": [
                    {
                        "symbol": mapping.provider_symbol,
                        "instrument_name": mapping.expected_name,
                        "instrument_type": mapping.instrument_type.value,
                        "exchange": mapping.exchange,
                        "currency": mapping.currency,
                        "access": {
                            "plan": "Basic",
                            "plan_business": "Basic",
                        },
                    }
                ],
            },
        )

    verification = asyncio.run(
        TwelveDataProvider(
            configuration(), transport=httpx.MockTransport(handler)
        ).verify()
    )

    assert verification.state is VerificationState.ERROR
    assert verification.provider_verified is False
    assert verification.customer_prices_available is False


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("exchange", "Binance"),
        ("instrument_type", "ETF"),
        ("symbol", "ETH/USD"),
    ],
)
def test_btc_identity_mismatch_is_unverified(field: str, value: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api_usage":
            return response(200, usage_payload())
        symbol = request.url.params["symbol"]
        mapping = next(
            item
            for item in configuration().mappings
            if item.provider_symbol == symbol
        )
        item = {
            "symbol": mapping.provider_symbol,
            "instrument_name": mapping.expected_name,
            "instrument_type": mapping.instrument_type.value,
            "exchange": mapping.exchange,
            "currency": mapping.currency,
            "access": {
                "global": "Basic",
                "plan": "Basic",
                "plan_business": "Basic",
            },
        }
        if symbol == "BTC/USD":
            item[field] = value
        return response(200, {"status": "ok", "data": [item]})

    verification = asyncio.run(
        TwelveDataProvider(
            configuration(),
            transport=httpx.MockTransport(handler),
        ).verify()
    )
    btc = next(
        item
        for item in verification.instruments
        if item.canonical is CanonicalInstrument.BTCUSD
    )

    assert btc.state is VerificationState.SYMBOL_UNVERIFIED
    assert verification.customer_prices_available is False


@pytest.mark.parametrize("instrument_type", ["ETF", "CFD", "Common Stock"])
def test_spx_proxy_types_are_rejected(instrument_type: str) -> None:
    provider = TwelveDataProvider(configuration())
    mapping = next(
        item
        for item in configuration().mappings
        if item.canonical is CanonicalInstrument.SPX
    )
    result = provider._mapping_result(
        mapping,
        {
            "status": "ok",
            "data": [
                {
                    "symbol": "SPX",
                    "instrument_name": "S&P 500 Index",
                    "instrument_type": instrument_type,
                    "exchange": "CBOE",
                    "currency": "USD",
                    "access": {
                        "global": "Basic",
                        "plan": "Basic",
                        "plan_business": "Basic",
                    },
                }
            ],
        },
    )

    assert result.state is VerificationState.SYMBOL_UNVERIFIED
    assert result.available is False


def test_provenance_freshness_contract_is_timezone_aware() -> None:
    provider_timestamp = datetime(2026, 8, 12, tzinfo=timezone.utc)
    provenance = MarketDataProvenance(
        canonical=CanonicalInstrument.BTCUSD,
        provider=MarketDataProviderName.TWELVE_DATA,
        source=MarketDataSource.TWELVE_DATA,
        provider_symbol="BTC/USD",
        exchange="COINBASE PRO",
        provider_timestamp=provider_timestamp,
        received_at=provider_timestamp + timedelta(seconds=1),
        stale_after_seconds=120,
    )

    assert (
        provenance.freshness_at(provider_timestamp + timedelta(seconds=120))
        is FreshnessState.FRESH
    )
    assert (
        provenance.freshness_at(provider_timestamp + timedelta(seconds=121))
        is FreshnessState.STALE
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        provenance.freshness_at(datetime(2026, 8, 12))
    with pytest.raises(ValidationError, match="timezone-aware"):
        MarketDataProvenance(
            **{
                **provenance.model_dump(),
                "provider_timestamp": datetime(2026, 8, 12),
            }
        )


def test_missing_provider_timestamp_is_unknown() -> None:
    received_at = datetime(2026, 8, 12, tzinfo=timezone.utc)
    provenance = MarketDataProvenance(
        canonical=CanonicalInstrument.XAUUSD,
        provider=MarketDataProviderName.TWELVE_DATA,
        source=MarketDataSource.TWELVE_DATA,
        provider_symbol="XAU/USD",
        exchange="COMMODITY",
        provider_timestamp=None,
        received_at=received_at,
        stale_after_seconds=120,
    )

    assert provenance.freshness_at(received_at) is FreshnessState.UNKNOWN
