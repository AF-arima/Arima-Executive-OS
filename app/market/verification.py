from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import MarketProviderVerification
from app.database.repositories.market_verification import (
    MarketProviderVerificationRepository,
)
from app.market.provider import MarketDataProvider, ProviderVerification


class MarketVerificationService:
    """Run metadata verification and persist only normalized state."""

    def __init__(self, session: AsyncSession) -> None:
        self._repository = MarketProviderVerificationRepository(session)

    async def verify_and_record(
        self, provider: MarketDataProvider
    ) -> tuple[ProviderVerification, Sequence[MarketProviderVerification]]:
        verification = await provider.verify()
        rows = await self._repository.record(verification)
        return verification, rows
