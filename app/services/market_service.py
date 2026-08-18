from sqlalchemy.ext.asyncio import AsyncSession

from app.data_engine.market_data import LegacyMarketDataAccessDisabled


async def fetch_and_store_market_price(
    db: AsyncSession,
    symbol: str,
) -> None:
    """Fail closed; the legacy price table is preserved but no longer written."""
    del db, symbol
    raise LegacyMarketDataAccessDisabled(
        "Legacy market-price persistence is retired; use MarketDataGateway"
    )
