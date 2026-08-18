"""Retired legacy direct market-data path.

Production consumers must use ``app.market.MarketDataGateway``.  This module
is retained only so historical imports fail closed rather than silently
reintroducing an unaudited provider dependency.
"""


class LegacyMarketDataAccessDisabled(RuntimeError):
    """Raised when code attempts to use the retired direct-provider path."""


def get_market_price(symbol: str) -> None:
    del symbol
    raise LegacyMarketDataAccessDisabled(
        "Direct market-data access is retired; use MarketDataGateway"
    )
