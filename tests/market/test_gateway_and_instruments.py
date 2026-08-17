import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.market import (
    CanonicalInstrument,
    InstrumentResolver,
    MarketDataConfiguration,
    MarketDataProviderRegistry,
)


@pytest.mark.parametrize(
    ("phrase", "canonical"),
    [
        ("Bitcoin right now", CanonicalInstrument.BTCUSD),
        ("price of ETH", CanonicalInstrument.ETHUSD),
        ("price of gold", CanonicalInstrument.XAUUSD),
        ("price of silver", CanonicalInstrument.XAGUSD),
        ("WTI doing", CanonicalInstrument.WTIUSD),
        ("Brent crude", CanonicalInstrument.BCOUSD),
        ("EUR/USD", CanonicalInstrument.EURUSD),
        ("GBP/USD", CanonicalInstrument.GBPUSD),
        ("Apple trading", CanonicalInstrument.AAPL),
        ("Tesla trading", CanonicalInstrument.TSLA),
        ("S&P 500", CanonicalInstrument.SPX),
        ("FTSE 100", CanonicalInstrument.FTSE100),
        ("SPY ETF", CanonicalInstrument.SPY),
    ],
)
def test_closed_catalog_resolves_supported_market_language(phrase: str, canonical: CanonicalInstrument) -> None:
    result = InstrumentResolver().resolve(phrase)
    assert result is not None
    assert result.canonical is canonical


@pytest.mark.parametrize("phrase", ["oil", "some made up coin", "Nasdaq"])
def test_resolver_fails_closed_for_ambiguous_or_unknown_instruments(phrase: str) -> None:
    assert InstrumentResolver().resolve(phrase) is None


@pytest.mark.parametrize("provider", ["twelve_data", "alpha_vantage"])
def test_provider_registry_selects_only_configured_adapter(provider: str) -> None:
    config = MarketDataConfiguration.from_settings(
        Settings(_env_file=None, market_data_provider=provider, market_data_source=provider)
    )
    selected = MarketDataProviderRegistry().create(config)
    assert selected.name.value == provider


def test_unknown_market_provider_is_rejected_by_configuration() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, market_data_provider="unknown")


def test_selected_provider_credential_is_independent_of_unused_provider() -> None:
    twelve = MarketDataConfiguration.from_settings(
        Settings(_env_file=None, market_data_provider="twelve_data", twelve_data_api_key="key")
    )
    alpha = MarketDataConfiguration.from_settings(
        Settings(_env_file=None, market_data_provider="alpha_vantage", alpha_vantage_api_key="key")
    )
    assert twelve.api_key is not None
    assert alpha.api_key is not None
