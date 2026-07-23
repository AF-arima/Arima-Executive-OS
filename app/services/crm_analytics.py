from datetime import datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import CRMActivityType, LeadSource, LeadStatus, User
from app.database.repositories.crm_analytics import CRMAnalyticsRepository
from app.schemas.crm import (
    CRMActivityAnalytics,
    CRMLeadAnalytics,
    CRMPipelineAnalytics,
    CRMTimeSeriesPoint,
)
from app.services.cache import crm_analytics_cache
from app.services.exceptions import InvalidAnalyticsRequestError
from app.services.permissions import crm_scope

UTC = timezone.utc
MAX_RANGE = timedelta(days=366 * 5)


class CRMAnalyticsService:
    def __init__(self, session: AsyncSession) -> None:
        self.repository = CRMAnalyticsRepository(session)

    async def pipeline(
        self,
        actor: User,
        *,
        start_date: datetime | None,
        end_date: datetime | None,
        refresh: bool = False,
    ) -> CRMPipelineAnalytics:
        start, end, now = self._range(start_date, end_date)
        key = self._key("pipeline", actor, start, end)
        if not refresh:
            cached = await crm_analytics_cache.get(key)
            if isinstance(cached, CRMPipelineAnalytics):
                return cached
        (
            open_value,
            weighted,
            counts,
            values,
            wins,
            losses,
            average,
            closes,
        ) = await self.repository.pipeline(
            crm_scope(actor), start=start, end=end
        )
        close_buckets: dict[datetime, Decimal] = {}
        cursor = self._day(start)
        while cursor <= end:
            close_buckets[cursor] = Decimal(0)
            cursor += timedelta(days=1)
        for period, value in closes:
            bucket = self._day(period)
            if bucket in close_buckets:
                close_buckets[bucket] += value
        response = CRMPipelineAnalytics(
            open_pipeline_value=open_value,
            weighted_pipeline_value=weighted,
            deal_count_by_stage=counts,
            value_by_stage=values,
            win_count=wins,
            loss_count=losses,
            win_rate=self._rate(wins, wins + losses),
            average_won_deal_size=average,
            expected_closes=[
                CRMTimeSeriesPoint(period_start=period, value=value)
                for period, value in sorted(close_buckets.items())
            ],
            generated_at=now,
        )
        await crm_analytics_cache.set(key, response)
        return response

    async def leads(
        self,
        actor: User,
        *,
        start_date: datetime | None,
        end_date: datetime | None,
        refresh: bool = False,
    ) -> CRMLeadAnalytics:
        start, end, now = self._range(start_date, end_date)
        key = self._key("leads", actor, start, end)
        if not refresh:
            cached = await crm_analytics_cache.get(key)
            if isinstance(cached, CRMLeadAnalytics):
                return cached
        statuses, sources, score, qualification_hours, conversion_hours = (
            await self.repository.leads(
                crm_scope(actor), start=start, end=end
            )
        )
        status_values = {item: statuses.get(item, 0) for item in LeadStatus}
        source_values = {item: sources.get(item, 0) for item in LeadSource}
        total = sum(status_values.values())
        qualified = (
            status_values[LeadStatus.QUALIFIED]
            + status_values[LeadStatus.CONVERTED]
        )
        response = CRMLeadAnalytics(
            leads_by_status=status_values,
            leads_by_source=source_values,
            qualification_rate=self._rate(qualified, total),
            conversion_rate=self._rate(
                status_values[LeadStatus.CONVERTED], total
            ),
            average_lead_score=round(score, 2),
            average_time_to_qualification_hours=round(
                qualification_hours, 2
            ),
            average_time_to_conversion_hours=round(conversion_hours, 2),
            lost_total=status_values[LeadStatus.LOST],
            disqualified_total=status_values[LeadStatus.DISQUALIFIED],
            generated_at=now,
        )
        await crm_analytics_cache.set(key, response)
        return response

    async def activities(
        self,
        actor: User,
        *,
        start_date: datetime | None,
        end_date: datetime | None,
        refresh: bool = False,
    ) -> CRMActivityAnalytics:
        start, end, now = self._range(start_date, end_date)
        key = self._key("activities", actor, start, end)
        if not refresh:
            cached = await crm_analytics_cache.get(key)
            if isinstance(cached, CRMActivityAnalytics):
                return cached
        scheduled, completed, overdue, types, timestamps = (
            await self.repository.activities(
                crm_scope(actor), start=start, end=end, now=now
            )
        )
        days: dict[datetime, Decimal] = {}
        cursor = start.replace(hour=0, minute=0, second=0, microsecond=0)
        while cursor <= end:
            days[cursor] = Decimal(0)
            cursor += timedelta(days=1)
        for raw_period, count in timestamps:
            if isinstance(raw_period, str):
                period = datetime.fromisoformat(raw_period).replace(tzinfo=UTC)
            else:
                period = self._day(raw_period)
            days[period] = days.get(period, Decimal(0)) + count
        response = CRMActivityAnalytics(
            scheduled=scheduled,
            completed=completed,
            overdue=overdue,
            activity_count_by_type={
                item: types.get(item, 0) for item in CRMActivityType
            },
            completion_rate=self._rate(completed, completed + scheduled),
            timeline=[
                CRMTimeSeriesPoint(period_start=period, value=value)
                for period, value in sorted(days.items())
            ],
            generated_at=now,
        )
        await crm_analytics_cache.set(key, response)
        return response

    @staticmethod
    def _range(
        start_date: datetime | None, end_date: datetime | None
    ) -> tuple[datetime, datetime, datetime]:
        now = datetime.now(UTC)
        end = end_date or (
            now.replace(second=0, microsecond=0) + timedelta(minutes=1)
        )
        start = start_date or end - timedelta(days=30)
        for value in (start, end):
            if value.tzinfo is None or value.utcoffset() is None:
                raise InvalidAnalyticsRequestError(
                    "Analytics dates must include a timezone"
                )
        start = start.astimezone(UTC)
        end = end.astimezone(UTC)
        if start > end:
            raise InvalidAnalyticsRequestError(
                "start_date must be before end_date"
            )
        if end - start > MAX_RANGE:
            raise InvalidAnalyticsRequestError(
                "CRM analytics range exceeds five years"
            )
        return start, end, now

    @staticmethod
    def _rate(numerator: int, denominator: int) -> float:
        return round(numerator / denominator, 4) if denominator else 0.0

    @staticmethod
    def _day(value: datetime) -> datetime:
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

    @staticmethod
    def _key(
        kind: str,
        actor: User,
        start: datetime,
        end: datetime,
    ) -> str:
        scope = crm_scope(actor)
        return "|".join(
            (
                "crm",
                kind,
                scope.kind.value,
                str(scope.user_id),
                ",".join(scope.roles),
                start.isoformat(),
                end.isoformat(),
            )
        )
