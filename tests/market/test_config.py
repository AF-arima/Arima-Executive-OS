import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.market.config import (
    CanonicalInstrument,
    InstrumentMapping,
    MarketDataConfiguration,
    MarketDataProviderName,
    MarketDataSource,
    TwelveDataInstrumentType,
)


def configured_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "twelve_data_api_key": "server-secret",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_market_data_configuration_uses_canonical_twelve_data_mappings() -> None:
    configuration = MarketDataConfiguration.from_settings(configured_settings())

    assert configuration.provider is MarketDataProviderName.TWELVE_DATA
    assert configuration.source is MarketDataSource.TWELVE_DATA
    assert [
        (
            mapping.canonical.value,
            mapping.provider_symbol,
            mapping.instrument_type.value,
            mapping.exchange,
            mapping.expected_name,
            mapping.currency,
        )
        for mapping in configuration.mappings
    ] == [
        (
            "XAUUSD",
            "XAU/USD",
            "Commodity",
            "COMMODITY",
            "Gold Spot / US Dollar",
            "USD",
        ),
        (
            "BTCUSD",
            "BTC/USD",
            "Digital Currency",
            "COINBASE PRO",
            "Bitcoin to US Dollar",
            "USD",
        ),
        ("SPX", "SPX", "Index", "CBOE", "S&P 500 Index", "USD"),
    ]
    assert configuration.account_plan.value == "basic"
    assert configuration.usage_scope.value == "internal_non_display"
    assert configuration.stale_after_seconds == 120
    assert configuration.timeout_seconds == 5.0
    assert configuration.rate_limit_per_minute == 8


def test_market_data_environment_configuration_is_server_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TWELVE_DATA_API_KEY", "environment-secret")
    monkeypatch.setenv("MARKET_DATA_TIMEOUT_SECONDS", "7.5")
    monkeypatch.setenv("MARKET_DATA_RATE_LIMIT_PER_MINUTE", "12")
    settings = Settings(_env_file=None)
    configuration = MarketDataConfiguration.from_settings(settings)

    assert configuration.api_key is not None
    assert configuration.api_key.get_secret_value() == "environment-secret"
    assert "environment-secret" not in repr(configuration)
    assert configuration.timeout_seconds == 7.5
    assert configuration.rate_limit_per_minute == 12


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("market_data_provider", "yahoo"),
        ("market_data_source", "mock"),
    ],
)
def test_settings_reject_unsupported_provider_or_source(
    name: str,
    value: str,
) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{name: value})


def test_mapping_rejects_wrong_instrument_type() -> None:
    with pytest.raises(ValidationError, match="Invalid instrument type"):
        InstrumentMapping(
            canonical=CanonicalInstrument.XAUUSD,
            provider=MarketDataProviderName.TWELVE_DATA,
            source=MarketDataSource.TWELVE_DATA,
            provider_symbol="XAU/USD",
            instrument_type=TwelveDataInstrumentType.DIGITAL_CURRENCY,
            exchange="Commodity",
            expected_name="Gold Spot / US Dollar",
            currency="USD",
        )


def test_mapping_rejects_spy_as_spx_identity() -> None:
    with pytest.raises(ValidationError, match="Invalid canonical identity"):
        InstrumentMapping(
            canonical=CanonicalInstrument.SPX,
            provider=MarketDataProviderName.TWELVE_DATA,
            source=MarketDataSource.TWELVE_DATA,
            provider_symbol="SPY",
            instrument_type=TwelveDataInstrumentType.INDEX,
            exchange="NYSE",
            expected_name="SPDR S&P 500 ETF Trust",
            currency="USD",
        )


def test_configuration_rejects_incomplete_or_duplicate_mappings() -> None:
    complete = MarketDataConfiguration.from_settings(configured_settings())
    with pytest.raises(ValidationError):
        MarketDataConfiguration(
            **{
                **complete.model_dump(),
                "mappings": (complete.mappings[0],) * 3,
            }
        )


def test_configuration_rejects_non_https_provider_url() -> None:
    with pytest.raises(ValidationError, match="must use HTTPS"):
        MarketDataConfiguration.from_settings(
            configured_settings(twelve_data_base_url="http://example.test")
        )


def test_configuration_limits_freshness_timeout_and_rate_limit() -> None:
    for override in (
        {"market_data_stale_after_seconds": 0},
        {"market_data_timeout_seconds": 31},
        {"market_data_rate_limit_per_minute": 3},
    ):
        with pytest.raises(ValidationError):
            configured_settings(**override)


def test_commercial_rights_require_a_server_side_reference() -> None:
    with pytest.raises(ValidationError, match="entitlement reference"):
        MarketDataConfiguration.from_settings(
            configured_settings(
                market_data_customer_display_entitled=True,
                market_data_real_time_entitled=True,
            )
        )


def test_redistribution_requires_display_rights() -> None:
    with pytest.raises(ValidationError, match="customer-display rights"):
        MarketDataConfiguration.from_settings(
            configured_settings(
                market_data_redistribution_entitled=True,
                market_data_real_time_entitled=True,
                market_data_entitlement_reference="contract-approval-id",
            )
        )


def test_entitlement_reference_is_secret_and_never_serialized_plaintext() -> None:
    configuration = MarketDataConfiguration.from_settings(
        configured_settings(
            market_data_customer_display_entitled=True,
            market_data_real_time_entitled=True,
            market_data_usage_scope="customer_display",
            market_data_entitlement_reference="contract-approval-id",
        )
    )

    assert "contract-approval-id" not in repr(configuration)
    assert "contract-approval-id" not in configuration.model_dump_json()
