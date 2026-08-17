import re
from enum import StrEnum
from functools import lru_cache

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

from app.core.config import Settings, get_settings


class MarketDataProviderName(StrEnum):
    TWELVE_DATA = "twelve_data"
    ALPHA_VANTAGE = "alpha_vantage"


class MarketDataSource(StrEnum):
    TWELVE_DATA = "twelve_data"
    ALPHA_VANTAGE = "alpha_vantage"


class MarketDataAccountPlan(StrEnum):
    BASIC = "basic"
    GROW = "grow"
    PRO = "pro"
    ULTRA = "ultra"
    VENTURE = "venture"
    ENTERPRISE = "enterprise"
    CUSTOM = "custom"


class MarketDataUsageScope(StrEnum):
    INTERNAL_NON_DISPLAY = "internal_non_display"
    CUSTOMER_DISPLAY = "customer_display"
    REDISTRIBUTION = "redistribution"


class CanonicalInstrument(StrEnum):
    XAUUSD = "XAUUSD"
    BTCUSD = "BTCUSD"
    SPX = "SPX"


class TwelveDataInstrumentType(StrEnum):
    COMMODITY = "Commodity"
    DIGITAL_CURRENCY = "Digital Currency"
    INDEX = "Index"


EXPECTED_INSTRUMENT_TYPES = {
    CanonicalInstrument.XAUUSD: TwelveDataInstrumentType.COMMODITY,
    CanonicalInstrument.BTCUSD: TwelveDataInstrumentType.DIGITAL_CURRENCY,
    CanonicalInstrument.SPX: TwelveDataInstrumentType.INDEX,
}
CANONICAL_IDENTITIES = {
    CanonicalInstrument.XAUUSD: (
        "XAU/USD",
        "COMMODITY",
        "Gold Spot / US Dollar",
        "USD",
    ),
    CanonicalInstrument.BTCUSD: (
        "BTC/USD",
        "COINBASE PRO",
        "Bitcoin to US Dollar",
        "USD",
    ),
    CanonicalInstrument.SPX: (
        "SPX",
        "CBOE",
        "S&P 500 Index",
        "USD",
    ),
}
PAIR_SYMBOL = re.compile(r"^[A-Z0-9]{2,15}/[A-Z0-9]{2,15}$")
SINGLE_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,49}$")


class InstrumentMapping(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    canonical: CanonicalInstrument
    provider: MarketDataProviderName
    source: MarketDataSource
    provider_symbol: str = Field(min_length=1, max_length=50)
    instrument_type: TwelveDataInstrumentType
    exchange: str = Field(min_length=1, max_length=50)
    expected_name: str = Field(min_length=1, max_length=200)
    currency: str = Field(min_length=3, max_length=3)

    @field_validator("provider_symbol", "exchange", "currency")
    @classmethod
    def normalize_identifiers(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("expected_name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def validate_mapping_contract(self) -> "InstrumentMapping":
        if self.source.value != self.provider.value:
            raise ValueError("The source must match the selected provider")
        expected_type = EXPECTED_INSTRUMENT_TYPES[self.canonical]
        if self.instrument_type is not expected_type:
            raise ValueError(
                f"Invalid instrument type for {self.canonical.value}"
            )
        if self.provider is MarketDataProviderName.ALPHA_VANTAGE:
            alpha_identities = {
                CanonicalInstrument.BTCUSD: (
                    "BTC", "ALPHA VANTAGE", "Bitcoin to US Dollar", "USD"
                ),
                CanonicalInstrument.XAUUSD: (
                    "XAU", "ALPHA VANTAGE", "Gold Spot / US Dollar", "USD"
                ),
                CanonicalInstrument.SPX: (
                    "SPX", "ALPHA VANTAGE", "S&P 500 Index", "USD"
                ),
            }
            expected_symbol, expected_exchange, expected_name, expected_currency = alpha_identities[self.canonical]
        else:
            expected_symbol, expected_exchange, expected_name, expected_currency = CANONICAL_IDENTITIES[self.canonical]
        if (
            self.provider_symbol,
            self.exchange,
            self.expected_name,
            self.currency,
        ) != (
            expected_symbol,
            expected_exchange,
            expected_name,
            expected_currency,
        ):
            raise ValueError(
                f"Invalid canonical identity for {self.canonical.value}"
            )
        if self.provider is MarketDataProviderName.TWELVE_DATA and self.canonical in {
            CanonicalInstrument.XAUUSD,
            CanonicalInstrument.BTCUSD,
        }:
            if not PAIR_SYMBOL.fullmatch(self.provider_symbol):
                raise ValueError(
                    f"{self.canonical.value} must map to a slash-delimited pair"
                )
            expected_base = self.canonical.value[:-3]
            if self.provider_symbol != f"{expected_base}/USD":
                raise ValueError(
                    f"{self.canonical.value} must preserve its canonical pair"
                )
        elif self.provider is MarketDataProviderName.TWELVE_DATA and not SINGLE_SYMBOL.fullmatch(self.provider_symbol):
            raise ValueError("SPX must map to a valid single symbol")
        return self


class MarketDataConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: MarketDataProviderName
    source: MarketDataSource
    account_plan: MarketDataAccountPlan
    usage_scope: MarketDataUsageScope
    customer_display_entitled: bool = False
    redistribution_entitled: bool = False
    real_time_entitled: bool = False
    entitlement_reference: SecretStr | None = None
    api_key: SecretStr | None = None
    base_url: AnyHttpUrl
    mappings: tuple[InstrumentMapping, ...] = Field(
        min_length=3, max_length=3
    )
    stale_after_seconds: int = Field(ge=1, le=86_400)
    timeout_seconds: float = Field(ge=0.1, le=30.0)
    rate_limit_per_minute: int = Field(ge=4, le=10_000)

    @model_validator(mode="after")
    def validate_complete_mapping_set(self) -> "MarketDataConfiguration":
        if self.source.value != self.provider.value:
            raise ValueError("Market data provider and source must match")
        canonicals = [mapping.canonical for mapping in self.mappings]
        if set(canonicals) != set(CanonicalInstrument):
            raise ValueError("Mappings must cover XAUUSD, BTCUSD, and SPX exactly")
        if len(canonicals) != len(set(canonicals)):
            raise ValueError("Canonical instrument mappings must be unique")
        provider_keys = [
            (mapping.provider_symbol, mapping.exchange)
            for mapping in self.mappings
        ]
        if len(provider_keys) != len(set(provider_keys)):
            raise ValueError("Provider instrument mappings must be unique")
        if any(mapping.provider is not self.provider for mapping in self.mappings):
            raise ValueError("Every mapping must use the selected provider")
        if any(mapping.source is not self.source for mapping in self.mappings):
            raise ValueError("Every mapping must use the selected source")
        if self.base_url.scheme != "https":
            raise ValueError("Market data provider base URL must use HTTPS")
        reference = self.entitlement_reference
        has_reference = bool(
            reference is not None
            and reference.get_secret_value().strip()
        )
        if (
            self.customer_display_entitled
            or self.redistribution_entitled
            or self.real_time_entitled
        ) and not has_reference:
            raise ValueError(
                "Commercial market-data rights require a server-side "
                "entitlement reference"
            )
        if self.redistribution_entitled and not self.customer_display_entitled:
            raise ValueError(
                "Redistribution entitlement requires customer-display rights"
            )
        if (
            self.usage_scope is MarketDataUsageScope.CUSTOMER_DISPLAY
            and not self.customer_display_entitled
        ):
            raise ValueError(
                "Customer-display scope requires verified commercial rights"
            )
        if (
            self.usage_scope is MarketDataUsageScope.REDISTRIBUTION
            and not self.redistribution_entitled
        ):
            raise ValueError(
                "Redistribution scope requires verified redistribution rights"
            )
        return self

    @classmethod
    def from_settings(cls, settings: Settings) -> "MarketDataConfiguration":
        provider = MarketDataProviderName(settings.market_data_provider)
        source = MarketDataSource(settings.market_data_source)
        return cls(
            provider=provider,
            source=source,
            account_plan=MarketDataAccountPlan(
                settings.market_data_account_plan
            ),
            usage_scope=MarketDataUsageScope(
                settings.market_data_usage_scope
            ),
            customer_display_entitled=(
                settings.market_data_customer_display_entitled
            ),
            redistribution_entitled=(
                settings.market_data_redistribution_entitled
            ),
            real_time_entitled=settings.market_data_real_time_entitled,
            entitlement_reference=(
                settings.market_data_entitlement_reference
            ),
            api_key=(
                settings.alpha_vantage_api_key
                if provider is MarketDataProviderName.ALPHA_VANTAGE
                else settings.twelve_data_api_key
            ),
            base_url=AnyHttpUrl(
                settings.alpha_vantage_base_url
                if provider is MarketDataProviderName.ALPHA_VANTAGE
                else settings.twelve_data_base_url
            ),
            mappings=(
                InstrumentMapping(
                    canonical=CanonicalInstrument.XAUUSD,
                    provider_symbol=(
                        "XAU"
                        if provider is MarketDataProviderName.ALPHA_VANTAGE
                        else settings.market_data_xauusd_symbol
                    ),
                    instrument_type=TwelveDataInstrumentType.COMMODITY,
                    exchange=(
                        "ALPHA VANTAGE"
                        if provider is MarketDataProviderName.ALPHA_VANTAGE
                        else settings.market_data_xauusd_exchange
                    ),
                    expected_name="Gold Spot / US Dollar",
                    currency="USD",
                    provider=provider,
                    source=source,
                ),
                InstrumentMapping(
                    canonical=CanonicalInstrument.BTCUSD,
                    provider_symbol=(
                        "BTC"
                        if provider is MarketDataProviderName.ALPHA_VANTAGE
                        else settings.market_data_btcusd_symbol
                    ),
                    instrument_type=(
                        TwelveDataInstrumentType.DIGITAL_CURRENCY
                    ),
                    exchange=(
                        "ALPHA VANTAGE"
                        if provider is MarketDataProviderName.ALPHA_VANTAGE
                        else settings.market_data_btcusd_exchange
                    ),
                    expected_name="Bitcoin to US Dollar",
                    currency="USD",
                    provider=provider,
                    source=source,
                ),
                InstrumentMapping(
                    canonical=CanonicalInstrument.SPX,
                    provider_symbol=(
                        "SPX"
                        if provider is MarketDataProviderName.ALPHA_VANTAGE
                        else settings.market_data_spx_symbol
                    ),
                    instrument_type=TwelveDataInstrumentType.INDEX,
                    exchange=(
                        "ALPHA VANTAGE"
                        if provider is MarketDataProviderName.ALPHA_VANTAGE
                        else settings.market_data_spx_exchange
                    ),
                    expected_name="S&P 500 Index",
                    currency="USD",
                    provider=provider,
                    source=source,
                ),
            ),
            stale_after_seconds=settings.market_data_stale_after_seconds,
            timeout_seconds=settings.market_data_timeout_seconds,
            rate_limit_per_minute=(
                settings.market_data_rate_limit_per_minute
            ),
        )


@lru_cache
def get_market_data_configuration() -> MarketDataConfiguration:
    return MarketDataConfiguration.from_settings(get_settings())
