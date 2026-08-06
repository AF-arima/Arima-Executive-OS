from fastapi import APIRouter
from app.data_engine.market_data import get_market_price


router = APIRouter(
    prefix="/market",
    tags=["Market Data"]
)


@router.get("/{symbol}")
async def market_price(symbol: str):
    data = get_market_price(symbol)

    if not data:
        return {
            "error": "No market data found"
        }

    return data
