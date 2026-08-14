from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.market.config import (
    InstrumentMapping,
    MarketDataConfiguration,
    MarketDataProviderName,
)
from app.market.provider import (
    EntitlementState,
    FreshnessState,
    InstrumentVerification,
    MarketDataProvider,
    ProviderVerification,
    VerificationState,
)

PLAN_RANK = {
    "basic": 0,
    "grow": 1,
    "venture": 1,
    "pro": 2,
    "enterprise": 2,
    "ultra": 3,
    "custom": 3,
}


class TwelveDataAccessPayload(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    global_: str = Field(min_length=1)
    plan: str = Field(min_length=1)
    plan_business: str = Field(min_length=1)

    def __init__(self, **data: Any) -> None:
        if "global" in data:
            data["global_"] = data.pop("global")
        super().__init__(**data)


class TwelveDataSymbolPayload(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    symbol: str
    instrument_name: str
    exchange: str
    instrument_type: str
    currency: str
    access: TwelveDataAccessPayload


class TwelveDataSearchPayload(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    status: str
    data: list[TwelveDataSymbolPayload]


class TwelveDataUsagePayload(BaseModel):
    model_config = ConfigDict(extra="ignore", strict=True)

    status: str
    timestamp: str
    current_usage: int = Field(ge=0)
    plan_limit: int = Field(gt=0)
    plan_category: str = Field(min_length=1)


class TwelveDataQuotePayload(BaseModel):
    """Only identity and timestamp are retained from the quote response."""

    model_config = ConfigDict(extra="ignore", strict=True)

    symbol: str
    name: str
    exchange: str
    currency: str
    timestamp: int = Field(gt=0)


class TwelveDataProvider(MarketDataProvider):
    """Non-price Twelve Data authentication and metadata verifier."""

    def __init__(
        self,
        configuration: MarketDataConfiguration,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(configuration)
        if self.name is not MarketDataProviderName.TWELVE_DATA:
            raise ValueError("TwelveDataProvider requires Twelve Data configuration")
        self._transport = transport

    def _result(
        self,
        state: VerificationState,
        reason: str,
        *,
        configured: bool,
        authenticated: bool = False,
        provider_verified: bool = False,
        account_plan_verified: bool = False,
        provider_account_plan: str | None = None,
        instruments: tuple[InstrumentVerification, ...] | None = None,
    ) -> ProviderVerification:
        return ProviderVerification(
            provider=self.name,
            source=self.source,
            state=state,
            configured=configured,
            authenticated=authenticated,
            provider_verified=provider_verified,
            account_plan_verified=account_plan_verified,
            provider_account_plan=provider_account_plan,
            checked_at=datetime.now(UTC),
            instruments=instruments or tuple(
                InstrumentVerification(
                    canonical=mapping.canonical,
                    state=state,
                    reason=reason,
                )
                for mapping in self.configuration.mappings
            ),
            reason=reason,
        )

    def _mapping_result(
        self,
        mapping: InstrumentMapping,
        payload: Mapping[str, Any],
        *,
        account_plan_verified: bool = False,
    ) -> InstrumentVerification:
        try:
            search = TwelveDataSearchPayload.model_validate(payload)
        except ValidationError:
            return InstrumentVerification(
                canonical=mapping.canonical,
                state=VerificationState.ERROR,
                reason="provider_response_malformed",
            )
        if search.status != "ok":
            return InstrumentVerification(
                canonical=mapping.canonical,
                state=VerificationState.SYMBOL_UNVERIFIED,
                reason="provider_symbol_search_failed",
            )

        exact = next(
            (item for item in search.data if self._matches_mapping(mapping, item)),
            None,
        )
        if exact is None:
            return InstrumentVerification(
                canonical=mapping.canonical,
                state=VerificationState.SYMBOL_UNVERIFIED,
                reason="provider_identity_unverified",
            )

        minimum_plan = exact.access.plan
        minimum_business_plan = exact.access.plan_business
        plan_entitled = self._plan_entitled(
            self.configuration.account_plan.value,
            minimum_plan,
            minimum_business_plan,
        )
        if not plan_entitled:
            state = VerificationState.ENTITLEMENT_UNVERIFIED
            entitlement = EntitlementState.PLAN_NOT_ENTITLED
            reason = "provider_plan_entitlement_missing"
        elif (
            self.configuration.customer_display_entitled
            and self.configuration.real_time_entitled
        ):
            state = VerificationState.VERIFIED_CUSTOMER_DISPLAY
            entitlement = (
                EntitlementState.REDISTRIBUTION
                if self.configuration.redistribution_entitled
                else EntitlementState.CUSTOMER_DISPLAY
            )
            reason = None
        else:
            state = VerificationState.VERIFIED_INTERNAL
            entitlement = EntitlementState.INTERNAL_NON_DISPLAY_ONLY
            reason = "customer_display_entitlement_missing"

        return InstrumentVerification(
            canonical=mapping.canonical,
            state=state,
            provider_verified=True,
            symbol_verified=True,
            source_verified=True,
            account_plan_verified=account_plan_verified,
            real_time_verified=(
                plan_entitled and self.configuration.real_time_entitled
            ),
            catalog_access_verified=True,
            provider_symbol=exact.symbol,
            provider_exchange=exact.exchange,
            provider_instrument_type=exact.instrument_type,
            provider_currency=exact.currency,
            global_access_level=exact.access.global_,
            minimum_plan=minimum_plan,
            minimum_business_plan=minimum_business_plan,
            entitlement_state=entitlement,
            customer_display_entitled=(
                plan_entitled
                and self.configuration.customer_display_entitled
                and self.configuration.real_time_entitled
            ),
            redistribution_entitled=(
                plan_entitled
                and self.configuration.redistribution_entitled
                and self.configuration.real_time_entitled
            ),
            reason=reason,
        )

    def _freshness_result(
        self,
        mapping: InstrumentMapping,
        instrument: InstrumentVerification,
        payload: Mapping[str, Any],
        *,
        checked_at: datetime,
    ) -> InstrumentVerification:
        try:
            quote = TwelveDataQuotePayload.model_validate(payload)
        except ValidationError:
            return instrument.model_copy(
                update={
                    "state": VerificationState.ERROR,
                    "freshness": FreshnessState.UNKNOWN,
                    "real_time_verified": False,
                    "reason": "provider_freshness_response_malformed",
                }
            )
        if (
            quote.symbol != mapping.provider_symbol
            or quote.name != mapping.expected_name
            or quote.exchange.upper() != mapping.exchange
            or quote.currency != mapping.currency
        ):
            return instrument.model_copy(
                update={
                    "state": VerificationState.SYMBOL_UNVERIFIED,
                    "freshness": FreshnessState.UNKNOWN,
                    "real_time_verified": False,
                    "reason": "provider_quote_identity_unverified",
                }
            )
        provider_timestamp = datetime.fromtimestamp(quote.timestamp, UTC)
        age_seconds = (checked_at - provider_timestamp).total_seconds()
        if age_seconds < 0:
            return instrument.model_copy(
                update={
                    "state": VerificationState.ERROR,
                    "freshness": FreshnessState.UNKNOWN,
                    "real_time_verified": False,
                    "reason": "provider_timestamp_in_future",
                }
            )
        freshness = (
            FreshnessState.FRESH
            if age_seconds <= self.configuration.stale_after_seconds
            else FreshnessState.STALE
        )
        if freshness is FreshnessState.STALE:
            return instrument.model_copy(
                update={
                    "state": VerificationState.STALE,
                    "freshness": freshness,
                    "provider_timestamp": provider_timestamp,
                    "real_time_verified": False,
                    "customer_display_entitled": False,
                    "redistribution_entitled": False,
                    "reason": "provider_observation_stale",
                }
            )
        return instrument.model_copy(
            update={
                "freshness": freshness,
                "provider_timestamp": provider_timestamp,
                "real_time_verified": (
                    instrument.account_plan_verified
                    and self.configuration.real_time_entitled
                ),
            }
        )

    @staticmethod
    def _matches_mapping(
        mapping: InstrumentMapping,
        item: TwelveDataSymbolPayload,
    ) -> bool:
        return (
            item.symbol == mapping.provider_symbol
            and item.instrument_name == mapping.expected_name
            and item.instrument_type == mapping.instrument_type.value
            and item.exchange.upper() == mapping.exchange
            and item.currency == mapping.currency
        )

    @staticmethod
    def _plan_entitled(
        configured_plan: str,
        minimum_individual_plan: str,
        minimum_business_plan: str,
    ) -> bool:
        configured_rank = PLAN_RANK.get(configured_plan.lower())
        required = (
            minimum_business_plan
            if configured_plan.lower() in {"venture", "enterprise", "custom"}
            else minimum_individual_plan
        )
        required_rank = PLAN_RANK.get(required.lower())
        return (
            configured_rank is not None
            and required_rank is not None
            and configured_rank >= required_rank
        )

    async def verify(self) -> ProviderVerification:
        health = self.configuration_health()
        if not health.configured:
            return self._result(
                VerificationState.NOT_CONFIGURED,
                health.issues[0],
                configured=False,
            )

        assert self.configuration.api_key is not None
        headers = {
            "Authorization": (
                "apikey "
                f"{self.configuration.api_key.get_secret_value()}"
            )
        }
        try:
            async with httpx.AsyncClient(
                base_url=str(self.configuration.base_url).rstrip("/"),
                headers=headers,
                timeout=httpx.Timeout(self.configuration.timeout_seconds),
                transport=self._transport,
            ) as client:
                usage = await client.get("/api_usage")
                if usage.status_code in {401, 403}:
                    return self._result(
                        VerificationState.AUTHENTICATION_FAILED,
                        "provider_authentication_failed",
                        configured=True,
                    )
                if usage.status_code == 429:
                    return self._result(
                        VerificationState.RATE_LIMITED,
                        "provider_rate_limited",
                        configured=True,
                    )
                usage.raise_for_status()
                usage_data = usage.json()
                if (
                    isinstance(usage_data, dict)
                    and usage_data.get("status") == "error"
                ):
                    return self._result(
                        VerificationState.AUTHENTICATION_FAILED,
                        "provider_authentication_failed",
                        configured=True,
                    )
                usage_payload = TwelveDataUsagePayload.model_validate(
                    usage_data
                )
                if usage_payload.status != "ok":
                    return self._result(
                        VerificationState.ERROR,
                        "provider_response_malformed",
                        configured=True,
                    )
                account_plan_verified = bool(usage_payload.plan_category) and (
                    usage_payload.plan_category.lower()
                    == self.configuration.account_plan.value
                )
                if not account_plan_verified:
                    return self._result(
                        VerificationState.ENTITLEMENT_UNVERIFIED,
                        "provider_account_plan_mismatch",
                        configured=True,
                        authenticated=True,
                        account_plan_verified=False,
                        provider_account_plan=usage_payload.plan_category,
                    )

                instruments: list[InstrumentVerification] = []
                for mapping in self.configuration.mappings:
                    response = await client.get(
                        "/symbol_search",
                        params={
                            "symbol": mapping.provider_symbol,
                            "outputsize": 120,
                            "show_plan": True,
                        },
                    )
                    if response.status_code == 429:
                        return self._result(
                            VerificationState.RATE_LIMITED,
                            "provider_rate_limited",
                            configured=True,
                            authenticated=True,
                            account_plan_verified=True,
                            provider_account_plan=usage_payload.plan_category,
                        )
                    response.raise_for_status()
                    payload = response.json()
                    instruments.append(
                        self._mapping_result(
                            mapping,
                            payload if isinstance(payload, dict) else {},
                            account_plan_verified=account_plan_verified,
                        )
                    )
                checked_at = datetime.now(UTC)
                for index, mapping in enumerate(self.configuration.mappings):
                    if not self.configuration.real_time_entitled:
                        break
                    instrument = instruments[index]
                    if not instrument.mapping_verified:
                        continue
                    response = await client.get(
                        "/quote",
                        params={
                            "symbol": mapping.provider_symbol,
                            "exchange": mapping.exchange,
                            "timezone": "UTC",
                        },
                    )
                    if response.status_code == 429:
                        return self._result(
                            VerificationState.RATE_LIMITED,
                            "provider_rate_limited",
                            configured=True,
                            authenticated=True,
                            account_plan_verified=True,
                            provider_account_plan=usage_payload.plan_category,
                        )
                    if response.status_code == 403:
                        instruments[index] = instrument.model_copy(
                            update={
                                "state": VerificationState.ENTITLEMENT_UNVERIFIED,
                                "real_time_verified": False,
                                "reason": "provider_quote_entitlement_missing",
                            }
                        )
                        continue
                    response.raise_for_status()
                    payload = response.json()
                    instruments[index] = self._freshness_result(
                        mapping,
                        instrument,
                        payload if isinstance(payload, dict) else {},
                        checked_at=checked_at,
                    )
        except (httpx.TimeoutException, httpx.NetworkError):
            return self._result(
                VerificationState.PROVIDER_UNAVAILABLE,
                "provider_unavailable",
                configured=True,
            )
        except (httpx.HTTPError, ValueError, ValidationError):
            return self._result(
                VerificationState.ERROR,
                "provider_verification_error",
                configured=True,
            )

        instrument_tuple = tuple(instruments)
        if any(item.state is VerificationState.ERROR for item in instruments):
            state = VerificationState.ERROR
            reason = "provider_response_malformed"
        elif any(
            item.state is VerificationState.SYMBOL_UNVERIFIED
            for item in instruments
        ):
            state = VerificationState.SYMBOL_UNVERIFIED
            reason = "provider_identity_unverified"
        elif any(
            item.state is VerificationState.ENTITLEMENT_UNVERIFIED
            for item in instruments
        ):
            state = VerificationState.ENTITLEMENT_UNVERIFIED
            reason = "provider_plan_entitlement_missing"
        elif any(item.state is VerificationState.STALE for item in instruments):
            state = VerificationState.STALE
            reason = "provider_observation_stale"
        elif all(
            item.state is VerificationState.VERIFIED_CUSTOMER_DISPLAY
            for item in instruments
        ):
            state = VerificationState.VERIFIED_CUSTOMER_DISPLAY
            reason = None
        else:
            state = VerificationState.VERIFIED_INTERNAL
            reason = "customer_display_entitlement_missing"
        return self._result(
            state,
            reason or "verified",
            configured=True,
            authenticated=True,
            provider_verified=all(
                item.provider_verified for item in instruments
            ),
            account_plan_verified=True,
            provider_account_plan=usage_payload.plan_category,
            instruments=instrument_tuple,
        )
