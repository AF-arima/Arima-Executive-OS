from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx
from pydantic import ValidationError

from app.market.config import (
    CanonicalInstrument,
    InstrumentMapping,
    MarketDataConfiguration,
    MarketDataProviderName,
)
from app.market.provider import (
    EntitlementState,
    FreshnessState,
    InstrumentVerification,
    MarketDataProvider,
    MarketDataProvenance,
    ProviderVerification,
    VerificationState,
)


class AlphaVantageProvider(MarketDataProvider):
    """Server-side Alpha Vantage adapter for current BTC/USD and gold data."""

    def __init__(
        self,
        configuration: MarketDataConfiguration,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        super().__init__(configuration)
        if self.name is not MarketDataProviderName.ALPHA_VANTAGE:
            raise ValueError("AlphaVantageProvider requires Alpha Vantage configuration")
        self._transport = transport

    async def verify(self) -> ProviderVerification:
        health = self.configuration_health()
        if not health.configured:
            return self._result(
                VerificationState.NOT_CONFIGURED,
                health.issues[0],
                configured=False,
            )
        instruments: list[InstrumentVerification] = []
        for mapping in self.configuration.mappings:
            if mapping.canonical is CanonicalInstrument.SPX:
                continue
            try:
                _, provenance = await self._quote(mapping.canonical)
            except _AlphaVantageUnavailable as error:
                instruments.append(
                    InstrumentVerification(
                        canonical=mapping.canonical,
                        state=error.state,
                        reason=error.reason,
                    )
                )
                continue
            instruments.append(self._verified_instrument(mapping, provenance))

        verified = [
            item
            for item in instruments
            if item.state
            in {
                VerificationState.VERIFIED_INTERNAL,
                VerificationState.VERIFIED_CUSTOMER_DISPLAY,
            }
        ]
        customer_display_verified = bool(verified) and all(
            item.available for item in verified
        )
        state = (
            VerificationState.VERIFIED_CUSTOMER_DISPLAY
            if customer_display_verified
            else (
                VerificationState.VERIFIED_INTERNAL
                if verified
                else VerificationState.PROVIDER_UNAVAILABLE
            )
        )
        return self._result(
            state,
            None if verified else "alpha_vantage_no_supported_instrument_available",
            configured=True,
            authenticated=True,
            provider_verified=bool(verified),
            account_plan_verified=True,
            instruments=tuple(instruments),
        )

    async def current_price(
        self, canonical: CanonicalInstrument
    ) -> tuple[Decimal, MarketDataProvenance]:
        price, provenance = await self._quote(canonical)
        return price, provenance

    async def _quote(
        self, canonical: CanonicalInstrument
    ) -> tuple[Decimal, MarketDataProvenance]:
        if canonical is CanonicalInstrument.SPX:
            raise _AlphaVantageUnavailable(
                VerificationState.SYMBOL_UNVERIFIED,
                "alpha_vantage_instrument_unsupported",
            )
        mapping = next(
            (
                item
                for item in self.configuration.mappings
                if item.canonical is canonical
            ),
            None,
        )
        if mapping is None:
            raise _AlphaVantageUnavailable(
                VerificationState.SYMBOL_UNVERIFIED,
                "alpha_vantage_instrument_unsupported",
            )
        key = self.configuration.api_key
        if key is None or not key.get_secret_value().strip():
            raise _AlphaVantageUnavailable(
                VerificationState.NOT_CONFIGURED,
                "provider_credentials_missing",
            )
        received_at = datetime.now(UTC)
        params: dict[str, str] = {
            "function": (
                "CURRENCY_EXCHANGE_RATE"
                if canonical is CanonicalInstrument.BTCUSD
                else "GOLD_SILVER_SPOT"
            ),
            "apikey": key.get_secret_value(),
        }
        if canonical is CanonicalInstrument.BTCUSD:
            params.update({"from_currency": "BTC", "to_currency": "USD"})
        else:
            # GOLD_SILVER_SPOT documents only the metal symbol; do not send
            # unsupported parameters that could change provider semantics.
            params.update({"symbol": "GOLD"})
        try:
            async with httpx.AsyncClient(
                base_url=str(self.configuration.base_url).rstrip("/"),
                timeout=httpx.Timeout(self.configuration.timeout_seconds),
                transport=self._transport,
            ) as client:
                response = await client.get("/query", params=params)
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise _AlphaVantageUnavailable(
                VerificationState.PROVIDER_UNAVAILABLE,
                "alpha_vantage_request_failed",
            ) from error
        try:
            price, observed_at = self._parse_quote(canonical, body)
        except (KeyError, TypeError, ValueError, ValidationError) as error:
            reason = self._error_reason(body)
            raise _AlphaVantageUnavailable(
                VerificationState.ERROR,
                reason,
            ) from error
        if observed_at > received_at:
            raise _AlphaVantageUnavailable(
                VerificationState.ERROR,
                "provider_timestamp_in_future",
            )
        freshness = FreshnessState.FRESH if (
            received_at - observed_at
        ).total_seconds() <= self.configuration.stale_after_seconds else FreshnessState.STALE
        if freshness is not FreshnessState.FRESH:
            raise _AlphaVantageUnavailable(
                VerificationState.STALE,
                "provider_observation_stale",
            )
        return price, MarketDataProvenance(
            canonical=canonical,
            provider=self.name,
            source=self.source,
            provider_symbol=mapping.provider_symbol,
            exchange=mapping.exchange,
            provider_timestamp=observed_at,
            received_at=received_at,
            stale_after_seconds=self.configuration.stale_after_seconds,
        )

    @staticmethod
    def _parse_quote(
        canonical: CanonicalInstrument, body: Any
    ) -> tuple[Decimal, datetime]:
        if not isinstance(body, dict):
            raise ValueError("malformed_response")
        if canonical is CanonicalInstrument.BTCUSD:
            payload = body["Realtime Currency Exchange Rate"]
            price = Decimal(str(payload["5. Exchange Rate"]))
            # Alpha Vantage returns a timezone-naive wall-clock string and an
            # explicit timezone field for this endpoint.  Accept only UTC so
            # the freshness gate never infers a timezone.
            if str(payload["7. Time Zone"]).strip().upper() != "UTC":
                raise ValueError("bitcoin_timestamp_timezone_unverified")
            timestamp = datetime.fromisoformat(
                str(payload["6. Last Refreshed"])
            ).replace(tzinfo=UTC)
        else:
            payload = body
            if str(payload.get("metal", "")).lower() != "gold":
                raise ValueError("gold_response_identity_unverified")
            price = Decimal(str(payload["price"]))
            timestamp = datetime.fromisoformat(
                str(payload["timestamp"]).replace("Z", "+00:00")
            )
        if price <= 0 or timestamp.tzinfo is None:
            raise ValueError("invalid_quote")
        return price, timestamp.astimezone(UTC)

    @staticmethod
    def _error_reason(body: Any) -> str:
        if isinstance(body, dict):
            for key in ("Error Message", "Information", "Note"):
                if key in body:
                    return "alpha_vantage_provider_error"
        return "alpha_vantage_response_malformed"

    def _verified_instrument(
        self,
        mapping: InstrumentMapping,
        provenance: MarketDataProvenance,
    ) -> InstrumentVerification:
        customer_display_entitled = (
            self.configuration.customer_display_entitled
            and self.configuration.real_time_entitled
        )
        redistribution_entitled = (
            customer_display_entitled
            and self.configuration.redistribution_entitled
        )
        return InstrumentVerification(
            canonical=mapping.canonical,
            state=(
                VerificationState.VERIFIED_CUSTOMER_DISPLAY
                if customer_display_entitled
                else VerificationState.VERIFIED_INTERNAL
            ),
            provider_verified=True,
            symbol_verified=True,
            source_verified=True,
            account_plan_verified=True,
            real_time_verified=True,
            catalog_access_verified=True,
            freshness=FreshnessState.FRESH,
            provider_timestamp=provenance.provider_timestamp,
            provider_symbol=mapping.provider_symbol,
            provider_exchange=mapping.exchange,
            provider_instrument_type=mapping.instrument_type.value,
            provider_currency=mapping.currency,
            entitlement_state=(
                EntitlementState.REDISTRIBUTION
                if redistribution_entitled
                else (
                    EntitlementState.CUSTOMER_DISPLAY
                    if customer_display_entitled
                    else EntitlementState.INTERNAL_NON_DISPLAY_ONLY
                )
            ),
            customer_display_entitled=customer_display_entitled,
            redistribution_entitled=redistribution_entitled,
        )

    def _result(self, state: VerificationState, reason: str | None, **kwargs: Any) -> ProviderVerification:
        kwargs.setdefault("configured", False)
        kwargs.setdefault("authenticated", False)
        kwargs.setdefault("provider_verified", False)
        kwargs.setdefault("account_plan_verified", False)
        return ProviderVerification(
            provider=self.name,
            source=self.source,
            state=state,
            checked_at=datetime.now(UTC),
            instruments=kwargs.pop("instruments", ()),
            reason=reason,
            **kwargs,
        )


class _AlphaVantageUnavailable(RuntimeError):
    def __init__(self, state: VerificationState, reason: str) -> None:
        super().__init__(reason)
        self.state = state
        self.reason = reason
