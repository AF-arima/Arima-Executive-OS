from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    CRMActivity,
    CRMActivityType,
    Deal,
    DealStatus,
    Lead,
    LeadSource,
    LeadStatus,
    PipelineStage,
)
from app.database.repositories.crm import CRMRepository
from app.services.permissions import AnalyticsScope


class CRMAnalyticsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def pipeline(
        self, scope: AnalyticsScope, *, start: datetime, end: datetime
    ) -> tuple[
        Decimal,
        Decimal,
        dict[str, int],
        dict[str, Decimal],
        int,
        int,
        Decimal,
        list[tuple[datetime, Decimal]],
    ]:
        visibility = CRMRepository.visibility(Deal, scope)
        filters = (
            visibility,
            Deal.created_at >= start,
            Deal.created_at <= end,
            Deal.archived_at.is_(None),
        )
        row = (
            await self.session.execute(
                select(
                    func.coalesce(
                        func.sum(
                            case(
                                (Deal.status == DealStatus.OPEN, Deal.value),
                                else_=0,
                            )
                        ),
                        0,
                    ),
                    func.coalesce(
                        func.sum(
                            case(
                                (
                                    Deal.status == DealStatus.OPEN,
                                    Deal.value * Deal.probability / 100,
                                ),
                                else_=0,
                            )
                        ),
                        0,
                    ),
                    func.sum(
                        case((Deal.status == DealStatus.WON, 1), else_=0)
                    ),
                    func.sum(
                        case((Deal.status == DealStatus.LOST, 1), else_=0)
                    ),
                    func.coalesce(
                        func.avg(
                            case(
                                (Deal.status == DealStatus.WON, Deal.value)
                            )
                        ),
                        0,
                    ),
                ).where(*filters)
            )
        ).one()
        stage_rows = await self.session.execute(
            select(
                PipelineStage.name,
                func.count(Deal.id),
                func.coalesce(func.sum(Deal.value), 0),
            )
            .join(Deal, Deal.stage_id == PipelineStage.id)
            .where(*filters)
            .group_by(PipelineStage.id, PipelineStage.name)
        )
        close_rows = await self.session.execute(
            select(
                Deal.expected_close_date,
                func.coalesce(func.sum(Deal.value), 0),
            )
            .where(
                *filters,
                Deal.status == DealStatus.OPEN,
                Deal.expected_close_date.is_not(None),
            )
            .group_by(Deal.expected_close_date)
            .order_by(Deal.expected_close_date)
        )
        stages = list(stage_rows)
        return (
            Decimal(row[0]),
            Decimal(row[1]),
            {name: int(count) for name, count, _ in stages},
            {name: Decimal(value) for name, _, value in stages},
            int(row[2] or 0),
            int(row[3] or 0),
            Decimal(row[4]),
            [
                (period, Decimal(value))
                for period, value in close_rows
                if period is not None
            ],
        )

    async def leads(
        self, scope: AnalyticsScope, *, start: datetime, end: datetime
    ) -> tuple[
        dict[LeadStatus, int],
        dict[LeadSource, int],
        float,
        float,
        float,
    ]:
        filters = (
            CRMRepository.visibility(Lead, scope),
            Lead.created_at >= start,
            Lead.created_at <= end,
            Lead.archived_at.is_(None),
        )
        status_rows = await self.session.execute(
            select(Lead.status, func.count(Lead.id))
            .where(*filters)
            .group_by(Lead.status)
        )
        source_rows = await self.session.execute(
            select(Lead.source, func.count(Lead.id))
            .where(*filters)
            .group_by(Lead.source)
        )
        averages = (
            await self.session.execute(
                select(
                    func.coalesce(func.avg(Lead.score), 0),
                    func.coalesce(
                        func.avg(
                            self._hours(Lead.qualified_at, Lead.created_at)
                        ),
                        0,
                    ),
                    func.coalesce(
                        func.avg(
                            self._hours(Lead.converted_at, Lead.created_at)
                        ),
                        0,
                    ),
                ).where(*filters)
            )
        ).one()
        return (
            {status: int(count) for status, count in status_rows},
            {source: int(count) for source, count in source_rows},
            float(averages[0]),
            float(averages[1]),
            float(averages[2]),
        )

    async def activities(
        self,
        scope: AnalyticsScope,
        *,
        start: datetime,
        end: datetime,
        now: datetime,
    ) -> tuple[
        int,
        int,
        int,
        dict[CRMActivityType, int],
        list[tuple[Any, int]],
    ]:
        filters = (
            CRMRepository.visibility(CRMActivity, scope),
            CRMActivity.created_at >= start,
            CRMActivity.created_at <= end,
        )
        row = (
            await self.session.execute(
                select(
                    func.sum(
                        case(
                            (
                                and_(
                                    CRMActivity.due_at.is_not(None),
                                    CRMActivity.completed_at.is_(None),
                                ),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    func.sum(
                        case(
                            (CRMActivity.completed_at.is_not(None), 1),
                            else_=0,
                        )
                    ),
                    func.sum(
                        case(
                            (
                                and_(
                                    CRMActivity.due_at < now,
                                    CRMActivity.completed_at.is_(None),
                                ),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                ).where(*filters)
            )
        ).one()
        types = await self.session.execute(
            select(CRMActivity.type, func.count(CRMActivity.id))
            .where(*filters)
            .group_by(CRMActivity.type)
        )
        dialect = self.session.bind.dialect.name if self.session.bind else ""
        bucket = (
            func.strftime("%Y-%m-%d", CRMActivity.created_at)
            if dialect == "sqlite"
            else func.date_trunc("day", CRMActivity.created_at)
        )
        timeline = await self.session.execute(
            select(bucket, func.count(CRMActivity.id))
            .where(*filters)
            .group_by(bucket)
            .order_by(bucket)
        )
        return (
            int(row[0] or 0),
            int(row[1] or 0),
            int(row[2] or 0),
            {activity_type: int(count) for activity_type, count in types},
            [(period, int(count)) for period, count in timeline],
        )

    def _hours(self, end: Any, start: Any) -> Any:
        dialect = self.session.bind.dialect.name if self.session.bind else ""
        if dialect == "sqlite":
            return (func.julianday(end) - func.julianday(start)) * 24.0
        return func.extract("epoch", end - start) / 3600.0
