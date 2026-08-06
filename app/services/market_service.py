from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.market import MarketPrice
from app.data_engine.market_data import get_market_price


async def fetch_and_store_market_price(
    db: AsyncSession,
    symbol: str,
):
    data = get_market_price(symbol)

    if not data:
        return None

    market_price = MarketPrice(
        symbol=data["symbol"],
        price=data["price"],
        source="yahoo",
        market_timestamp=datetime.now(timezone.utc),
    )

    db.add(market_price)

    await db.commit()
    await db.refresh(market_price)

    return market_price
