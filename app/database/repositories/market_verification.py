from collections.abc import Sequence
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import MarketProviderVerification
from app.database.repositories.base import AsyncRepository
from app.market.provider import ProviderVerification


class MarketProviderVerificationRepository(
    AsyncRepository[MarketProviderVerification]
):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(MarketProviderVerification, session)

    async def record(
        self, verification: ProviderVerification, *, run_id: UUID | None = None
    ) -> Sequence[MarketProviderVerification]:
        run_id = run_id or uuid4()
        rows = [
            MarketProviderVerification(
                run_id=run_id,
                provider=verification.provider.value,
                source=verification.source.value,
                canonical=None,
                state=verification.state.value,
                configured=verification.configured,
                authenticated=verification.authenticated,
                provider_verified=verification.provider_verified,
                account_plan_verified=verification.account_plan_verified,
                symbol_verified=False,
                source_verified=False,
                real_time_verified=False,
                freshness="unknown",
                provider_timestamp=None,
                checked_at=verification.checked_at,
                reason=verification.reason,
            )
        ]
        rows.extend(
            MarketProviderVerification(
                run_id=run_id,
                provider=verification.provider.value,
                source=verification.source.value,
                canonical=instrument.canonical.value,
                state=instrument.state.value,
                configured=verification.configured,
                authenticated=verification.authenticated,
                provider_verified=instrument.provider_verified,
                account_plan_verified=instrument.account_plan_verified,
                symbol_verified=instrument.symbol_verified,
                source_verified=instrument.source_verified,
                real_time_verified=instrument.real_time_verified,
                freshness=instrument.freshness.value,
                provider_timestamp=instrument.provider_timestamp,
                checked_at=verification.checked_at,
                reason=instrument.reason,
            )
            for instrument in verification.instruments
        )
        self.session.add_all(rows)
        await self.session.flush()
        return rows

    async def for_run(
        self, run_id: UUID
    ) -> Sequence[MarketProviderVerification]:
        return (
            await self.session.scalars(
                select(MarketProviderVerification)
                .where(MarketProviderVerification.run_id == run_id)
                .order_by(MarketProviderVerification.canonical)
            )
        ).all()
