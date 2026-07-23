import json
from datetime import datetime, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.database.models import (
    ProjectStatus,
    TaskPriority,
    TaskStatus,
    User,
)
from app.database.repositories import (
    ActivityRepository,
    AnalyticsRepository,
)
from app.schemas.analytics import (
    AnalyticsInterval,
    DashboardSummary,
    ProjectAnalyticsItem,
    ProjectAnalyticsList,
    ProjectAnalyticsSortField,
    TaskAnalyticsResponse,
    TimeSeriesPoint,
    WorkloadAnalyticsItem,
    WorkloadAnalyticsList,
    WorkloadSortField,
)
from app.schemas.common import SortDirection
from app.services.cache import DashboardCache, dashboard_cache
from app.services.exceptions import InvalidAnalyticsRequestError
from app.services.permissions import analytics_scope, workload_scope

UTC = timezone.utc
DEFAULT_RANGE_DAYS = 30
MAX_GENERAL_RANGE = timedelta(days=366 * 5)
MAX_INTERVAL_RANGES = {
    AnalyticsInterval.DAY: timedelta(days=180),
    AnalyticsInterval.WEEK: timedelta(days=366 * 2),
    AnalyticsInterval.MONTH: timedelta(days=366 * 5),
}


class AnalyticsService:
    def __init__(
        self,
        session: AsyncSession,
        cache: DashboardCache | None = None,
    ) -> None:
        self.session = session
        self.repository = AnalyticsRepository(session)
        self.activity = ActivityRepository(session)
        self.cache = cache or dashboard_cache

    async def dashboard_summary(
        self,
        actor: User,
        *,
        start_date: datetime | None,
        end_date: datetime | None,
        project_id: UUID | None,
        owner_id: UUID | None,
        assigned_to: UUID | None,
        timezone_name: str,
        include_archived: bool,
        refresh: bool,
    ) -> DashboardSummary:
        now = datetime.now(UTC)
        zone = self._timezone(timezone_name)
        start, end = self.resolve_range(
            start_date,
            end_date,
            now=now,
            default_zone=zone,
            maximum=MAX_GENERAL_RANGE,
        )
        scope = analytics_scope(actor)
        key = self._dashboard_cache_key(
            actor=actor,
            scope_kind=scope.kind.value,
            roles=scope.roles,
            start=start,
            end=end,
            project_id=project_id,
            owner_id=owner_id,
            assigned_to=assigned_to,
            timezone_name=timezone_name,
            include_archived=include_archived,
        )
        if not refresh:
            cached = await self.cache.get(key)
            if cached is not None:
                return cached
        cache_generation = await self.cache.generation()

        raw = await self.repository.dashboard(
            scope,
            start=start,
            end=end,
            now=now,
            project_id=project_id,
            owner_id=owner_id,
            assigned_to=assigned_to,
            include_archived=include_archived,
        )
        recent_activity = await self.activity.count_recent(
            scope,
            start=start,
            end=end,
        )
        summary = DashboardSummary(
            total_projects=raw.total_projects,
            active_projects=raw.active_projects,
            archived_projects=raw.archived_projects,
            projects_by_status=raw.projects_by_status,
            total_tasks=raw.total_tasks,
            tasks_by_status=raw.tasks_by_status,
            tasks_by_priority=raw.tasks_by_priority,
            completed_tasks=raw.completed_tasks,
            overdue_tasks=raw.overdue_tasks,
            unassigned_tasks=raw.unassigned_tasks,
            completion_rate=self._rate(
                raw.completed_tasks,
                raw.total_tasks,
            ),
            overdue_rate=self._rate(
                raw.overdue_tasks,
                raw.total_tasks,
            ),
            average_completion_time_hours=round(
                raw.average_completion_time_hours,
                2,
            ),
            tasks_due_next_7_days=raw.tasks_due_next_7_days,
            tasks_due_next_30_days=raw.tasks_due_next_30_days,
            active_users=raw.active_users,
            recent_activity_count=recent_activity,
            generated_at=now,
            range_start=start,
            range_end=end,
        )
        await self.cache.set(
            key,
            summary,
            ttl_seconds=get_settings().dashboard_cache_ttl_seconds,
            expected_generation=cache_generation,
        )
        return summary

    async def project_analytics(
        self,
        actor: User,
        *,
        start_date: datetime | None,
        end_date: datetime | None,
        status: ProjectStatus | None,
        owner_id: UUID | None,
        include_archived: bool,
        search: str | None,
        sort_by: ProjectAnalyticsSortField,
        direction: SortDirection,
        limit: int,
        offset: int,
    ) -> ProjectAnalyticsList:
        now = datetime.now(UTC)
        start, end = self.resolve_range(
            start_date,
            end_date,
            now=now,
            maximum=MAX_GENERAL_RANGE,
        )
        rows, total = await self.repository.project_analytics(
            analytics_scope(actor),
            start=start,
            end=end,
            now=now,
            status=status,
            owner_id=owner_id,
            include_archived=include_archived,
            search=search,
            sort_by=sort_by,
            direction=direction,
            limit=limit,
            offset=offset,
        )
        return ProjectAnalyticsList(
            items=[
                ProjectAnalyticsItem(
                    project_id=row.project_id,
                    name=row.name,
                    status=row.status,
                    owner_id=row.owner_id,
                    archived=row.archived,
                    total_tasks=row.total_tasks,
                    completed_tasks=row.completed_tasks,
                    overdue_tasks=row.overdue_tasks,
                    unassigned_tasks=row.unassigned_tasks,
                    completion_rate=self._rate(
                        row.completed_tasks,
                        row.total_tasks,
                    ),
                    overdue_rate=self._rate(
                        row.overdue_tasks,
                        row.total_tasks,
                    ),
                    average_completion_time_hours=round(
                        row.average_completion_time_hours,
                        2,
                    ),
                    last_activity_at=row.last_activity_at,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                )
                for row in rows
            ],
            total=total,
            limit=limit,
            offset=offset,
        )

    async def task_analytics(
        self,
        actor: User,
        *,
        start_date: datetime | None,
        end_date: datetime | None,
        project_id: UUID | None,
        assigned_to: UUID | None,
        status: TaskStatus | None,
        priority: TaskPriority | None,
        interval: AnalyticsInterval,
    ) -> TaskAnalyticsResponse:
        now = datetime.now(UTC)
        start, end = self.resolve_range(
            start_date,
            end_date,
            now=now,
            maximum=MAX_INTERVAL_RANGES[interval],
        )
        raw = await self.repository.task_analytics(
            analytics_scope(actor),
            start=start,
            end=end,
            now=now,
            project_id=project_id,
            assigned_to=assigned_to,
            status=status,
            priority=priority,
            interval=interval,
        )
        periods = self._periods(start, end, interval)
        created = {period: 0 for period in periods}
        completed = {period: 0 for period in periods}
        overdue = {period: 0 for period in periods}
        created.update(raw.created_series)
        completed.update(raw.completed_series)
        overdue.update(raw.overdue_series)
        return TaskAnalyticsResponse(
            totals=raw.total,
            status_breakdown=raw.status_breakdown,
            priority_breakdown=raw.priority_breakdown,
            overdue_count=raw.overdue,
            completed_count=raw.completed,
            created_count=raw.total,
            completion_rate=self._rate(raw.completed, raw.total),
            average_completion_time_hours=round(
                raw.average_completion_time_hours,
                2,
            ),
            throughput_series=self._series(completed),
            created_series=self._series(created),
            overdue_series=self._series(overdue),
            generated_at=now,
            range_start=start,
            range_end=end,
            interval=interval,
        )

    async def workload_analytics(
        self,
        actor: User,
        *,
        project_id: UUID | None,
        role: str | None,
        active_only: bool,
        sort_by: WorkloadSortField,
        direction: SortDirection,
        limit: int,
        offset: int,
    ) -> WorkloadAnalyticsList:
        now = datetime.now(UTC)
        normalized_role = role.strip().lower() if role is not None else None
        if normalized_role == "":
            raise InvalidAnalyticsRequestError(
                "role cannot be blank"
            )
        rows, total = await self.repository.workload(
            workload_scope(actor),
            now=now,
            project_id=project_id,
            role=normalized_role,
            active_only=active_only,
            sort_by=sort_by,
            direction=direction,
            limit=limit,
            offset=offset,
        )
        return WorkloadAnalyticsList(
            items=[
                WorkloadAnalyticsItem(
                    user_id=row.user_id,
                    email=row.email,
                    active_task_count=row.active_task_count,
                    overdue_task_count=row.overdue_task_count,
                    completed_task_count=row.completed_task_count,
                    urgent_task_count=row.urgent_task_count,
                    high_priority_task_count=(
                        row.high_priority_task_count
                    ),
                    due_next_7_days=row.due_next_7_days,
                    average_completion_time_hours=round(
                        row.average_completion_time_hours,
                        2,
                    ),
                    workload_score=(
                        row.active_task_count
                        + row.overdue_task_count * 3
                        + row.urgent_task_count * 2
                        + row.high_priority_task_count
                        + row.due_next_7_days
                    ),
                )
                for row in rows
            ],
            total=total,
            limit=limit,
            offset=offset,
        )

    @staticmethod
    def resolve_range(
        start_date: datetime | None,
        end_date: datetime | None,
        *,
        now: datetime,
        default_zone: ZoneInfo | timezone = UTC,
        maximum: timedelta | None = None,
    ) -> tuple[datetime, datetime]:
        if start_date is not None:
            AnalyticsService._require_aware(start_date)
        if end_date is not None:
            AnalyticsService._require_aware(end_date)
        local_now = now.astimezone(default_zone).replace(
            second=0,
            microsecond=0,
        ) + timedelta(minutes=1)
        end = (end_date or local_now).astimezone(UTC)
        start = (
            start_date.astimezone(UTC)
            if start_date is not None
            else end - timedelta(days=DEFAULT_RANGE_DAYS)
        )
        if start > end:
            raise InvalidAnalyticsRequestError(
                "start_date must be before or equal to end_date"
            )
        if maximum is not None and end - start > maximum:
            raise InvalidAnalyticsRequestError(
                "Date range exceeds the maximum for this interval"
            )
        return start, end

    @staticmethod
    def _require_aware(value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise InvalidAnalyticsRequestError(
                "Datetime values must include a timezone"
            )

    @staticmethod
    def _timezone(name: str) -> ZoneInfo:
        try:
            return ZoneInfo(name)
        except ZoneInfoNotFoundError as error:
            raise InvalidAnalyticsRequestError(
                "Unknown timezone"
            ) from error

    @staticmethod
    def _rate(numerator: int, denominator: int) -> float:
        if denominator == 0:
            return 0.0
        return round(numerator / denominator, 6)

    @staticmethod
    def _bucket(
        value: datetime,
        interval: AnalyticsInterval,
    ) -> datetime:
        value = AnalyticsService._as_utc(value)
        if interval is AnalyticsInterval.DAY:
            return value.replace(hour=0, minute=0, second=0, microsecond=0)
        if interval is AnalyticsInterval.WEEK:
            start = value - timedelta(days=value.weekday())
            return start.replace(hour=0, minute=0, second=0, microsecond=0)
        return value.replace(
            day=1,
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )

    @staticmethod
    def _periods(
        start: datetime,
        end: datetime,
        interval: AnalyticsInterval,
    ) -> list[datetime]:
        current = AnalyticsService._bucket(start, interval)
        last = AnalyticsService._bucket(end, interval)
        periods: list[datetime] = []
        while current <= last:
            periods.append(current)
            if interval is AnalyticsInterval.DAY:
                current += timedelta(days=1)
            elif interval is AnalyticsInterval.WEEK:
                current += timedelta(days=7)
            elif current.month == 12:
                current = current.replace(
                    year=current.year + 1,
                    month=1,
                )
            else:
                current = current.replace(month=current.month + 1)
        return periods

    @staticmethod
    def _series(
        values: dict[datetime, int],
    ) -> list[TimeSeriesPoint]:
        return [
            TimeSeriesPoint(period_start=period, value=value)
            for period, value in values.items()
        ]

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    @staticmethod
    def _dashboard_cache_key(
        *,
        actor: User,
        scope_kind: str,
        roles: tuple[str, ...],
        start: datetime,
        end: datetime,
        project_id: UUID | None,
        owner_id: UUID | None,
        assigned_to: UUID | None,
        timezone_name: str,
        include_archived: bool,
    ) -> str:
        return json.dumps(
            {
                "user_id": str(actor.id),
                "scope": scope_kind,
                "roles": roles,
                "start": start.isoformat(),
                "end": end.isoformat(),
                "project_id": (
                    str(project_id) if project_id is not None else None
                ),
                "owner_id": (
                    str(owner_id) if owner_id is not None else None
                ),
                "assigned_to": (
                    str(assigned_to) if assigned_to is not None else None
                ),
                "timezone": timezone_name,
                "include_archived": include_archived,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
