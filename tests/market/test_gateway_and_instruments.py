import asyncio
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.data_engine.market_data import (
    LegacyMarketDataAccessDisabled,
    get_market_price,
)
from app.database.repositories import UserRepository, WorkspaceMembershipRepository
from app.market import (
    CanonicalInstrument,
    InstrumentResolver,
    MarketDataConfiguration,
    MarketDataGateway,
    MarketDataProviderRegistry,
    MarketDataUnavailableError,
    MarketDataAccessError,
)
from app.services.market_service import fetch_and_store_market_price
from tests.auth.conftest import AuthTestContext
from tests.auth.helpers import register_user


class NoProviderRegistry:
    """Spy registry proving a local rejection never reaches a provider."""

    def __init__(self) -> None:
        self.calls = 0

    def create(self, _: MarketDataConfiguration) -> object:
        self.calls += 1
        raise AssertionError("A locally denied request must not create a provider")


def configured_gateway_configuration() -> MarketDataConfiguration:
    return MarketDataConfiguration.from_settings(
        Settings(
            _env_file=None,
            market_data_real_time_entitled=True,
            market_data_customer_display_entitled=True,
            market_data_usage_scope="customer_display",
            market_data_entitlement_reference="approved-reference",
        )
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


def test_gateway_rejects_cross_workspace_before_provider_creation(
    auth_context: AuthTestContext,
) -> None:
    email = "market-gateway-tenant@example.com"
    register_user(auth_context, email)

    async def scenario() -> None:
        async with auth_context.session_factory() as session:
            user = await UserRepository(session).get_by_email(email)
            assert user is not None
            registry = NoProviderRegistry()
            gateway = MarketDataGateway(
                session, (configured_gateway_configuration(),), registry
            )
            with pytest.raises(MarketDataAccessError):
                await gateway.current_price(
                    canonical=CanonicalInstrument.BTCUSD,
                    user=user,
                    workspace_id=uuid4(),
                    run_id=uuid4(),
                )
            assert registry.calls == 0

    asyncio.run(scenario())


def test_gateway_denies_missing_display_entitlement_before_provider_creation(
    auth_context: AuthTestContext,
) -> None:
    email = "market-gateway-entitlement@example.com"
    register_user(auth_context, email)

    async def scenario() -> None:
        async with auth_context.session_factory() as session:
            user = await UserRepository(session).get_by_email(email)
            assert user is not None
            membership = await WorkspaceMembershipRepository(session).get_for_user(
                user.id
            )
            assert membership is not None
            registry = NoProviderRegistry()
            configuration = MarketDataConfiguration.from_settings(
                Settings(_env_file=None)
            )
            gateway = MarketDataGateway(session, (configuration,), registry)
            with pytest.raises(MarketDataUnavailableError, match="pre-approved"):
                await gateway.current_price(
                    canonical=CanonicalInstrument.BTCUSD,
                    user=user,
                    workspace_id=membership.workspace_id,
                    run_id=uuid4(),
                )
            assert registry.calls == 0

    asyncio.run(scenario())


def test_legacy_market_price_paths_are_quarantined() -> None:
    with pytest.raises(LegacyMarketDataAccessDisabled):
        get_market_price("BTC-USD")

    async def scenario() -> None:
        with pytest.raises(LegacyMarketDataAccessDisabled):
            await fetch_and_store_market_price(None, "BTC-USD")  # type: ignore[arg-type]

    asyncio.run(scenario())
