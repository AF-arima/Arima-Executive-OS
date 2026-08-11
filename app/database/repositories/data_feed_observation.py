from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import DataFeedObservation
from app.database.repositories.base import AsyncRepository


class DataFeedObservationRepository(AsyncRepository[DataFeedObservation]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(DataFeedObservation, session)

    async def latest_for_feed_keys(
        self,
        feed_keys: Sequence[str],
    ) -> dict[str, DataFeedObservation]:
        if not feed_keys:
            return {}
        observations = (
            await self.session.scalars(
                select(DataFeedObservation)
                .where(DataFeedObservation.feed_key.in_(feed_keys))
                .order_by(
                    DataFeedObservation.feed_key,
                    DataFeedObservation.observed_at.desc(),
                    DataFeedObservation.created_at.desc(),
                )
            )
        ).all()
        latest: dict[str, DataFeedObservation] = {}
        for observation in observations:
            latest.setdefault(observation.feed_key, observation)
        return latest
