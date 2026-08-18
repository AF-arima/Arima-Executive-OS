from abc import ABC, abstractmethod
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.market.config import (
    CanonicalInstrument,
    CONFIGURED_CANONICAL_INSTRUMENTS,
    MarketDataConfiguration,
    MarketDataProviderName,
    MarketDataSource,
)


class VerificationState(StrEnum):
    NOT_CONFIGURED = "not_configured"
    CONFIGURED = "configured"
    AUTHENTICATION_FAILED = "authentication_failed"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    SYMBOL_UNVERIFIED = "symbol_unverified"
    ENTITLEMENT_UNVERIFIED = "entitlement_unverified"
    VERIFIED_INTERNAL = "verified_internal"
    VERIFIED_CUSTOMER_DISPLAY = "verified_customer_display"
    STALE = "stale"
    RATE_LIMITED = "rate_limited"
    ERROR = "error"


class EntitlementState(StrEnum):
    UNVERIFIED = "unverified"
    INTERNAL_NON_DISPLAY_ONLY = "internal_non_display_only"
    PLAN_NOT_ENTITLED = "plan_not_entitled"
    CUSTOMER_DISPLAY = "customer_display"
    REDISTRIBUTION = "redistribution"


class FreshnessState(StrEnum):
    FRESH = "fresh"
    STALE = "stale"
    UNKNOWN = "unknown"


class ProviderConfigurationHealth(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: MarketDataProviderName
    source: MarketDataSource
    configured: bool
    state: VerificationState
    issues: tuple[str, ...] = ()


class InstrumentVerification(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    canonical: CanonicalInstrument
    state: VerificationState
    provider_verified: bool = False
    symbol_verified: bool = False
    source_verified: bool = False
    account_plan_verified: bool = False
    real_time_verified: bool = False
    catalog_access_verified: bool = False
    freshness: FreshnessState = FreshnessState.UNKNOWN
    provider_timestamp: datetime | None = None
    provider_symbol: str | None = None
    provider_exchange: str | None = None
    provider_instrument_type: str | None = None
    provider_currency: str | None = None
    global_access_level: str | None = None
    minimum_plan: str | None = None
    minimum_business_plan: str | None = None
    entitlement_state: EntitlementState = EntitlementState.UNVERIFIED
    customer_display_entitled: bool = False
    redistribution_entitled: bool = False
    reason: str | None = None

    @field_validator("provider_timestamp")
    @classmethod
    def require_provider_timestamp_timezone(
        cls, value: datetime | None
    ) -> datetime | None:
        if value is not None and (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise ValueError("Provider timestamp must be timezone-aware")
        return value

    @property
    def mapping_verified(self) -> bool:
        return self.symbol_verified and self.source_verified

    @property
    def available(self) -> bool:
        return (
            self.state is VerificationState.VERIFIED_CUSTOMER_DISPLAY
            and self.provider_verified
            and self.mapping_verified
            and self.real_time_verified
            and self.customer_display_entitled
        )


class ProviderVerification(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: MarketDataProviderName
    source: MarketDataSource
    state: VerificationState
    configured: bool
    authenticated: bool
    provider_verified: bool
    account_plan_verified: bool = False
    provider_account_plan: str | None = None
    checked_at: datetime
    instruments: tuple[InstrumentVerification, ...]
    reason: str | None = None

    @field_validator("checked_at")
    @classmethod
    def require_checked_at_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Verification timestamp must be timezone-aware")
        return value

    @property
    def customer_prices_available(self) -> bool:
        return (
            self.state is VerificationState.VERIFIED_CUSTOMER_DISPLAY
            and self.authenticated
            and self.provider_verified
            and bool(self.instruments)
            and all(instrument.available for instrument in self.instruments)
        )


class MarketDataProvenance(BaseModel):
    """Server-only provenance and freshness contract for observations."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    canonical: CanonicalInstrument
    provider: MarketDataProviderName
    source: MarketDataSource
    provider_symbol: str
    exchange: str
    provider_timestamp: datetime | None
    received_at: datetime
    stale_after_seconds: int = Field(ge=1, le=86_400)

    @field_validator("provider_timestamp", "received_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise ValueError("Provenance timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def reject_future_provider_timestamp(self) -> "MarketDataProvenance":
        if (
            self.provider_timestamp is not None
            and self.provider_timestamp > self.received_at
        ):
            raise ValueError("Provider timestamp cannot follow receipt time")
        return self

    def freshness_at(self, at: datetime) -> FreshnessState:
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("Freshness comparison must be timezone-aware")
        if self.provider_timestamp is None:
            return FreshnessState.UNKNOWN
        if at < self.provider_timestamp:
            raise ValueError("Freshness time cannot precede provider time")
        age_seconds = (at - self.provider_timestamp).total_seconds()
        return (
            FreshnessState.FRESH
            if age_seconds <= self.stale_after_seconds
            else FreshnessState.STALE
        )


class MarketDataProvider(ABC):
    def __init__(self, configuration: MarketDataConfiguration) -> None:
        self.configuration = configuration

    @property
    def name(self) -> MarketDataProviderName:
        return self.configuration.provider

    @property
    def source(self) -> MarketDataSource:
        return self.configuration.source

    def configuration_health(self) -> ProviderConfigurationHealth:
        issues: list[str] = []
        api_key = self.configuration.api_key
        if api_key is None or not api_key.get_secret_value().strip():
            issues.append("provider_credentials_missing")
        if len(self.configuration.mappings) != len(CONFIGURED_CANONICAL_INSTRUMENTS):
            issues.append("instrument_mappings_incomplete")
        return ProviderConfigurationHealth(
            provider=self.name,
            source=self.source,
            configured=not issues,
            state=(
                VerificationState.CONFIGURED
                if not issues
                else VerificationState.NOT_CONFIGURED
            ),
            issues=tuple(issues),
        )

    @abstractmethod
    async def verify(self) -> ProviderVerification:
        """Verify credentials and mappings without fetching market prices."""

    @abstractmethod
    async def current_price(
        self, canonical: CanonicalInstrument
    ) -> tuple[Decimal, MarketDataProvenance]:
        """Return a price only after the gateway has selected this provider."""
