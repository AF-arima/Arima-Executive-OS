from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.v1.dependencies import AUTHENTICATED_RESPONSES
from app.auth.dependencies import get_current_active_user
from app.database.models import User
from app.market.config import (
    MarketDataConfiguration,
    get_market_data_configuration,
)
from app.schemas.market import (
    MarketAvailability,
    MarketSymbol,
    MarketSymbolAvailability,
)

router = APIRouter(
    prefix="/market",
    tags=["market"],
    responses=AUTHENTICATED_RESPONSES,
)
MarketUser = Annotated[User, Depends(get_current_active_user)]
MarketConfiguration = Annotated[
    MarketDataConfiguration,
    Depends(get_market_data_configuration),
]

UNVERIFIED_PROVIDER_REASON = "No verified market data provider is configured."
SUPPORTED_SYMBOLS: tuple[MarketSymbol, ...] = ("XAUUSD", "BTCUSD", "SPX")


@router.get(
    "/availability",
    response_model=MarketAvailability,
    summary="Get market data availability",
    description=(
        "Reports whether a verified market data provider is available for "
        "the supported symbols. This endpoint does not return market data."
    ),
)
async def market_availability(
    _: MarketUser,
    configuration: MarketConfiguration,
) -> MarketAvailability:
    # Configuration is resolved server-side so invalid mappings fail closed.
    # Provider diagnostics and credentials are intentionally never serialized.
    return MarketAvailability(
        symbols=[
            MarketSymbolAvailability(
                symbol=symbol,
                available=False,
                provider=None,
                reason=UNVERIFIED_PROVIDER_REASON,
            )
            for symbol in SUPPORTED_SYMBOLS
        ]
    )
