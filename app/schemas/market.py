from typing import Literal

from app.schemas.auth import StrictSchema

MarketSymbol = Literal["XAUUSD", "BTCUSD", "SPX"]


class MarketSymbolAvailability(StrictSchema):
    symbol: MarketSymbol
    available: Literal[False]
    provider: None
    reason: str


class MarketAvailability(StrictSchema):
    symbols: list[MarketSymbolAvailability]
