from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    DeliveryEventType,
    DraftStatus,
    QueueStatus,
    User,
)
from app.database.repositories.outreach import OutreachRepository
from app.schemas.outreach import OutreachAnalytics
from app.services.cache import outreach_analytics_cache
from app.services.permissions import outreach_scope

UTC = timezone.utc


class OutreachAnalyticsService:
    def __init__(self, session: AsyncSession) -> None:
        self.repository = OutreachRepository(session)

    async def summary(self, actor: User, *, refresh: bool = False) -> OutreachAnalytics:
        key = f"outreach:{actor.id}:{outreach_scope(actor).kind.value}"
        if not refresh:
            cached = await outreach_analytics_cache.get(key)
            if isinstance(cached, OutreachAnalytics):
                return cached
        draft_raw, queue_raw, event_raw = await self.repository.analytics(
            outreach_scope(actor)
        )
        drafts = {status: draft_raw.get(status, 0) for status in DraftStatus}
        queue = {status: queue_raw.get(status, 0) for status in QueueStatus}
        events = {event: event_raw.get(event, 0) for event in DeliveryEventType}
        sent = max(
            queue[QueueStatus.SENT],
            events[DeliveryEventType.SENT],
        )
        response = OutreachAnalytics(
            drafts_by_status=drafts,
            queue_by_status=queue,
            events_by_type=events,
            sent=sent,
            delivered=events[DeliveryEventType.DELIVERED],
            opened=events[DeliveryEventType.OPENED],
            clicked=events[DeliveryEventType.CLICKED],
            replied=events[DeliveryEventType.REPLIED],
            bounced=events[DeliveryEventType.BOUNCED],
            unsubscribed=events[DeliveryEventType.UNSUBSCRIBED],
            delivery_rate=self._rate(events[DeliveryEventType.DELIVERED], sent),
            open_rate=self._rate(events[DeliveryEventType.OPENED], sent),
            click_rate=self._rate(events[DeliveryEventType.CLICKED], sent),
            reply_rate=self._rate(events[DeliveryEventType.REPLIED], sent),
            generated_at=datetime.now(UTC),
        )
        await outreach_analytics_cache.set(key, response)
        return response

    @staticmethod
    def _rate(value: int, total: int) -> float:
        return round(value / total, 4) if total else 0.0
