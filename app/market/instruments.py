"""Controlled, provider-neutral market instrument resolution.

This is intentionally a closed catalog: natural language is never turned into
an arbitrary provider symbol or a web-search request.
"""

from enum import StrEnum
import re

from pydantic import BaseModel, ConfigDict

from app.market.config import CanonicalInstrument


class AssetClass(StrEnum):
    CRYPTO = "crypto"
    FX = "fx"
    EQUITY = "equity"
    INDEX = "index"
    COMMODITY = "commodity"
    ETF = "etf"


class MarketDataField(StrEnum):
    PRICE = "price"


class CanonicalInstrumentRequest(BaseModel):
    """Provider-independent identity requested by orchestration."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    canonical: CanonicalInstrument
    asset_class: AssetClass
    name: str
    requested_field: MarketDataField = MarketDataField.PRICE
    base_currency: str | None = None
    quote_currency: str | None = None
    venue: str | None = None


_CATALOG: tuple[tuple[CanonicalInstrumentRequest, tuple[str, ...]], ...] = (
    (CanonicalInstrumentRequest(canonical=CanonicalInstrument.BTCUSD, asset_class=AssetClass.CRYPTO, name="Bitcoin / US Dollar", base_currency="BTC", quote_currency="USD"), ("bitcoin", "btc", "btc/usd", "بیت کوین", "بیت‌کوین", "بیتکوین")),
    (CanonicalInstrumentRequest(canonical=CanonicalInstrument.ETHUSD, asset_class=AssetClass.CRYPTO, name="Ether / US Dollar", base_currency="ETH", quote_currency="USD"), ("ethereum", "ether", "eth", "eth/usd")),
    (CanonicalInstrumentRequest(canonical=CanonicalInstrument.XAUUSD, asset_class=AssetClass.COMMODITY, name="Gold Spot / US Dollar", base_currency="XAU", quote_currency="USD"), ("gold", "xau", "xau/usd")),
    (CanonicalInstrumentRequest(canonical=CanonicalInstrument.XAGUSD, asset_class=AssetClass.COMMODITY, name="Silver Spot / US Dollar", base_currency="XAG", quote_currency="USD"), ("silver", "xag", "xag/usd")),
    (CanonicalInstrumentRequest(canonical=CanonicalInstrument.WTIUSD, asset_class=AssetClass.COMMODITY, name="WTI Crude Oil", quote_currency="USD"), ("wti", "west texas intermediate")),
    (CanonicalInstrumentRequest(canonical=CanonicalInstrument.BCOUSD, asset_class=AssetClass.COMMODITY, name="Brent Crude Oil", quote_currency="USD"), ("brent", "brent crude")),
    (CanonicalInstrumentRequest(canonical=CanonicalInstrument.HGUSD, asset_class=AssetClass.COMMODITY, name="Copper", quote_currency="USD"), ("copper",)),
    (CanonicalInstrumentRequest(canonical=CanonicalInstrument.EURUSD, asset_class=AssetClass.FX, name="Euro / US Dollar", base_currency="EUR", quote_currency="USD"), ("eur/usd", "eurusd", "euro dollar")),
    (CanonicalInstrumentRequest(canonical=CanonicalInstrument.GBPUSD, asset_class=AssetClass.FX, name="Pound Sterling / US Dollar", base_currency="GBP", quote_currency="USD"), ("gbp/usd", "gbpusd", "pound dollar")),
    (CanonicalInstrumentRequest(canonical=CanonicalInstrument.USDJPY, asset_class=AssetClass.FX, name="US Dollar / Japanese Yen", base_currency="USD", quote_currency="JPY"), ("usd/jpy", "usdjpy", "dollar yen")),
    (CanonicalInstrumentRequest(canonical=CanonicalInstrument.AAPL, asset_class=AssetClass.EQUITY, name="Apple Inc.", venue="NASDAQ"), ("apple", "aapl")),
    (CanonicalInstrumentRequest(canonical=CanonicalInstrument.MSFT, asset_class=AssetClass.EQUITY, name="Microsoft Corporation", venue="NASDAQ"), ("microsoft", "msft")),
    (CanonicalInstrumentRequest(canonical=CanonicalInstrument.TSLA, asset_class=AssetClass.EQUITY, name="Tesla, Inc.", venue="NASDAQ"), ("tesla", "tsla")),
    (CanonicalInstrumentRequest(canonical=CanonicalInstrument.SPX, asset_class=AssetClass.INDEX, name="S&P 500 Index", venue="CBOE"), ("s&p 500", "spx", "s and p 500")),
    (CanonicalInstrumentRequest(canonical=CanonicalInstrument.NDX, asset_class=AssetClass.INDEX, name="Nasdaq-100 Index"), ("nasdaq-100", "nasdaq 100", "ndx")),
    (CanonicalInstrumentRequest(canonical=CanonicalInstrument.FTSE100, asset_class=AssetClass.INDEX, name="FTSE 100 Index"), ("ftse 100", "ftse")),
    (CanonicalInstrumentRequest(canonical=CanonicalInstrument.DAX, asset_class=AssetClass.INDEX, name="DAX Index"), ("dax",)),
    (CanonicalInstrumentRequest(canonical=CanonicalInstrument.SPY, asset_class=AssetClass.ETF, name="SPDR S&P 500 ETF Trust", venue="NYSE Arca"), ("spy", "spdr s&p 500 etf")),
)


class InstrumentResolver:
    def resolve(self, text: str) -> CanonicalInstrumentRequest | None:
        value = re.sub(r"[^\w/& ]+", " ", text.casefold()).replace("\u200c", " ")
        if re.search(r"\boil\b", value) and not any(word in value for word in ("wti", "brent", "west texas")):
            return None
        matches = [request for request, aliases in _CATALOG if any(re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", value) for alias in aliases)]
        return matches[0] if len(matches) == 1 else None
