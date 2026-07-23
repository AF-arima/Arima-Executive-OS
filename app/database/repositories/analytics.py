from dataclasses import dataclass
from datetime import date as date_type
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    ColumnElement,
    ColumnExpressionArgument,
    Date,
    Integer,
    String,
    and_,
    case,
    cast,
    exists,
    extract,
    func,
    literal,
    or_,
    select,
    true,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    AuditLog,
    Project,
    ProjectStatus,
    Role,
    Task,
    TaskPriority,
    TaskStatus,
    User,
    UserRole,
)
from app.database.repositories.pagination import escape_like
from app.schemas.analytics import (
    AnalyticsInterval,
    ProjectAnalyticsSortField,
    WorkloadSortField,
)
from app.schemas.common import SortDirection
from app.services.permissions import AnalyticsScope, VisibilityKind


@dataclass(frozen=True, slots=True)
class DashboardRaw:
    total_projects: int
    active_projects: int
    archived_projects: int
    projects_by_status: dict[ProjectStatus, int]
    total_tasks: int
    tasks_by_status: dict[TaskStatus, int]
    tasks_by_priority: dict[TaskPriority, int]
    completed_tasks: int
    overdue_tasks: int
    unassigned_tasks: int
    average_completion_time_hours: float
    tasks_due_next_7_days: int
    tasks_due_next_30_days: int
    active_users: int


@dataclass(frozen=True, slots=True)
class ProjectAnalyticsRow:
    project_id: UUID
    name: str
    status: ProjectStatus
    owner_id: UUID
    archived: bool
    total_tasks: int
    completed_tasks: int
    overdue_tasks: int
    unassigned_tasks: int
    average_completion_time_hours: float
    last_activity_at: datetime | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class TaskAnalyticsRaw:
    total: int
    completed: int
    overdue: int
    average_completion_time_hours: float
    status_breakdown: dict[TaskStatus, int]
    priority_breakdown: dict[TaskPriority, int]
    created_series: dict[datetime, int]
    completed_series: dict[datetime, int]
    overdue_series: dict[datetime, int]


@dataclass(frozen=True, slots=True)
class WorkloadRow:
    user_id: UUID
    email: str
    active_task_count: int
    overdue_task_count: int
    completed_task_count: int
    urgent_task_count: int
    high_priority_task_count: int
    due_next_7_days: int
    average_completion_time_hours: float


class AnalyticsRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def dashboard(
        self,
        scope: AnalyticsScope,
        *,
        start: datetime,
        end: datetime,
        now: datetime,
        project_id: UUID | None,
        owner_id: UUID | None,
        assigned_to: UUID | None,
        include_archived: bool,
    ) -> DashboardRaw:
        project_condition = self.project_visibility(scope)
        task_condition = self.task_visibility(scope)
        project_filters: list[ColumnElement[bool]] = [
            project_condition,
            Project.created_at >= start,
            Project.created_at <= end,
        ]
        task_filters: list[ColumnElement[bool]] = [
            task_condition,
            Task.created_at >= start,
            Task.created_at <= end,
        ]
        if not include_archived:
            project_filters.append(Project.archived_at.is_(None))
            task_filters.append(Project.archived_at.is_(None))
        if project_id is not None:
            project_filters.append(Project.id == project_id)
            task_filters.append(Task.project_id == project_id)
        if owner_id is not None:
            project_filters.append(Project.owner_id == owner_id)
            task_filters.append(Project.owner_id == owner_id)
        if assigned_to is not None:
            assigned_projects = select(Task.project_id).where(
                Task.assignee_id == assigned_to
            )
            project_filters.append(Project.id.in_(assigned_projects))
            task_filters.append(Task.assignee_id == assigned_to)

        project_rows = await self.session.execute(
            select(Project.status, Project.archived_at, func.count())
            .where(*project_filters)
            .group_by(Project.status, Project.archived_at)
        )
        projects_by_status = {status: 0 for status in ProjectStatus}
        total_projects = 0
        archived_projects = 0
        for status, archived_at, count in project_rows:
            value = int(count)
            total_projects += value
            projects_by_status[status] += value
            if archived_at is not None:
                archived_projects += value

        completed_case = case(
            (Task.completed_at.is_not(None), 1),
            else_=0,
        )
        overdue_case = case(
            (
                and_(
                    Task.due_date < now,
                    Task.completed_at.is_(None),
                ),
                1,
            ),
            else_=0,
        )
        unassigned_case = case(
            (Task.assignee_id.is_(None), 1),
            else_=0,
        )
        due_7_case = case(
            (
                and_(
                    Task.due_date >= now,
                    Task.due_date <= now + timedelta(days=7),
                    Task.completed_at.is_(None),
                ),
                1,
            ),
            else_=0,
        )
        due_30_case = case(
            (
                and_(
                    Task.due_date >= now,
                    Task.due_date <= now + timedelta(days=30),
                    Task.completed_at.is_(None),
                ),
                1,
            ),
            else_=0,
        )
        task_summary = (
            await self.session.execute(
                select(
                    func.count(Task.id),
                    func.coalesce(func.sum(completed_case), 0),
                    func.coalesce(func.sum(overdue_case), 0),
                    func.coalesce(func.sum(unassigned_case), 0),
                    func.coalesce(
                        func.avg(self._completion_hours()),
                        0.0,
                    ),
                    func.coalesce(func.sum(due_7_case), 0),
                    func.coalesce(func.sum(due_30_case), 0),
                )
                .select_from(Task)
                .join(Project, Project.id == Task.project_id)
                .where(*task_filters)
            )
        ).one()
        status_rows = await self.session.execute(
            select(Task.status, func.count(Task.id))
            .join(Project, Project.id == Task.project_id)
            .where(*task_filters)
            .group_by(Task.status)
        )
        priority_rows = await self.session.execute(
            select(Task.priority, func.count(Task.id))
            .join(Project, Project.id == Task.project_id)
            .where(*task_filters)
            .group_by(Task.priority)
        )
        tasks_by_status = {status: 0 for status in TaskStatus}
        for status, count in status_rows:
            tasks_by_status[status] = int(count)
        tasks_by_priority = {priority: 0 for priority in TaskPriority}
        for priority, count in priority_rows:
            tasks_by_priority[priority] = int(count)

        active_users = await self._active_user_count(
            scope,
            task_filters=task_filters,
        )
        return DashboardRaw(
            total_projects=total_projects,
            active_projects=projects_by_status[ProjectStatus.ACTIVE],
            archived_projects=archived_projects,
            projects_by_status=projects_by_status,
            total_tasks=int(task_summary[0]),
            completed_tasks=int(task_summary[1]),
            overdue_tasks=int(task_summary[2]),
            unassigned_tasks=int(task_summary[3]),
            average_completion_time_hours=float(task_summary[4]),
            tasks_due_next_7_days=int(task_summary[5]),
            tasks_due_next_30_days=int(task_summary[6]),
            tasks_by_status=tasks_by_status,
            tasks_by_priority=tasks_by_priority,
            active_users=active_users,
        )

    async def project_analytics(
        self,
        scope: AnalyticsScope,
        *,
        start: datetime,
        end: datetime,
        now: datetime,
        status: ProjectStatus | None,
        owner_id: UUID | None,
        include_archived: bool,
        search: str | None,
        sort_by: ProjectAnalyticsSortField,
        direction: SortDirection,
        limit: int,
        offset: int,
    ) -> tuple[list[ProjectAnalyticsRow], int]:
        task_filters = [
            self.task_visibility(scope),
            Task.created_at >= start,
            Task.created_at <= end,
        ]
        task_aggregate = (
            select(
                Task.project_id.label("project_id"),
                func.count(Task.id).label("total_tasks"),
                func.coalesce(
                    func.sum(
                        case(
                            (Task.completed_at.is_not(None), 1),
                            else_=0,
                        )
                    ),
                    0,
                ).label("completed_tasks"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                and_(
                                    Task.due_date < now,
                                    Task.completed_at.is_(None),
                                ),
                                1,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("overdue_tasks"),
                func.coalesce(
                    func.sum(
                        case(
                            (Task.assignee_id.is_(None), 1),
                            else_=0,
                        )
                    ),
                    0,
                ).label("unassigned_tasks"),
                func.coalesce(
                    func.avg(self._completion_hours()),
                    0.0,
                ).label("average_completion_time_hours"),
            )
            .join(Project, Project.id == Task.project_id)
            .where(*task_filters)
            .group_by(Task.project_id)
            .subquery()
        )
        activity_aggregate = self._project_activity_subquery(start, end)
        total_tasks = func.coalesce(task_aggregate.c.total_tasks, 0)
        completed_tasks = func.coalesce(
            task_aggregate.c.completed_tasks,
            0,
        )
        overdue_tasks = func.coalesce(
            task_aggregate.c.overdue_tasks,
            0,
        )
        completion_rate = completed_tasks / func.nullif(total_tasks, 0)
        filters = self._project_analytics_filters(
            scope,
            start=start,
            end=end,
            status=status,
            owner_id=owner_id,
            include_archived=include_archived,
            search=search,
        )
        columns: dict[ProjectAnalyticsSortField, Any] = {
            ProjectAnalyticsSortField.NAME: Project.name,
            ProjectAnalyticsSortField.CREATED_AT: Project.created_at,
            ProjectAnalyticsSortField.UPDATED_AT: Project.updated_at,
            ProjectAnalyticsSortField.TOTAL_TASKS: total_tasks,
            ProjectAnalyticsSortField.COMPLETED_TASKS: completed_tasks,
            ProjectAnalyticsSortField.OVERDUE_TASKS: overdue_tasks,
            ProjectAnalyticsSortField.COMPLETION_RATE: func.coalesce(
                completion_rate,
                0.0,
            ),
            ProjectAnalyticsSortField.LAST_ACTIVITY_AT: (
                activity_aggregate.c.last_activity_at
            ),
        }
        sort_column = columns[sort_by]
        ordering = (
            sort_column.asc().nulls_last()
            if direction is SortDirection.ASC
            else sort_column.desc().nulls_last()
        )
        id_ordering = (
            Project.id.asc()
            if direction is SortDirection.ASC
            else Project.id.desc()
        )
        statement = (
            select(
                Project.id,
                Project.name,
                Project.status,
                Project.owner_id,
                Project.archived_at,
                total_tasks,
                completed_tasks,
                overdue_tasks,
                func.coalesce(task_aggregate.c.unassigned_tasks, 0),
                func.coalesce(
                    task_aggregate.c.average_completion_time_hours,
                    0.0,
                ),
                activity_aggregate.c.last_activity_at,
                Project.created_at,
                Project.updated_at,
            )
            .outerjoin(
                task_aggregate,
                task_aggregate.c.project_id == Project.id,
            )
            .outerjoin(
                activity_aggregate,
                activity_aggregate.c.project_id == Project.id,
            )
            .where(*filters)
            .order_by(ordering, id_ordering)
            .limit(limit)
            .offset(offset)
        )
        rows = await self.session.execute(statement)
        total = await self.session.scalar(
            select(func.count(Project.id)).where(*filters)
        )
        return (
            [
                ProjectAnalyticsRow(
                    project_id=row[0],
                    name=row[1],
                    status=row[2],
                    owner_id=row[3],
                    archived=row[4] is not None,
                    total_tasks=int(row[5]),
                    completed_tasks=int(row[6]),
                    overdue_tasks=int(row[7]),
                    unassigned_tasks=int(row[8]),
                    average_completion_time_hours=float(row[9]),
                    last_activity_at=row[10],
                    created_at=row[11],
                    updated_at=row[12],
                )
                for row in rows
            ],
            int(total or 0),
        )

    async def task_analytics(
        self,
        scope: AnalyticsScope,
        *,
        start: datetime,
        end: datetime,
        now: datetime,
        project_id: UUID | None,
        assigned_to: UUID | None,
        status: TaskStatus | None,
        priority: TaskPriority | None,
        interval: AnalyticsInterval,
        include_archived: bool = False,
    ) -> TaskAnalyticsRaw:
        filters = self._task_analytics_filters(
            scope,
            start=start,
            end=end,
            project_id=project_id,
            assigned_to=assigned_to,
            status=status,
            priority=priority,
            include_archived=include_archived,
        )
        summary = (
            await self.session.execute(
                select(
                    func.count(Task.id),
                    func.coalesce(
                        func.sum(
                            case(
                                (Task.completed_at.is_not(None), 1),
                                else_=0,
                            )
                        ),
                        0,
                    ),
                    func.coalesce(
                        func.sum(
                            case(
                                (
                                    and_(
                                        Task.due_date < now,
                                        Task.completed_at.is_(None),
                                    ),
                                    1,
                                ),
                                else_=0,
                            )
                        ),
                        0,
                    ),
                    func.coalesce(
                        func.avg(self._completion_hours()),
                        0.0,
                    ),
                )
                .select_from(Task)
                .join(Project, Project.id == Task.project_id)
                .where(*filters)
            )
        ).one()
        statuses = await self.session.execute(
            select(Task.status, func.count(Task.id))
            .join(Project, Project.id == Task.project_id)
            .where(*filters)
            .group_by(Task.status)
        )
        priorities = await self.session.execute(
            select(Task.priority, func.count(Task.id))
            .join(Project, Project.id == Task.project_id)
            .where(*filters)
            .group_by(Task.priority)
        )
        created_series = await self._group_series(
            filters,
            timestamp=Task.created_at,
            interval=interval,
        )
        completed_series = await self._group_series(
            filters,
            timestamp=Task.completed_at,
            interval=interval,
            extra_filters=[
                Task.completed_at >= start,
                Task.completed_at <= end,
            ],
        )
        overdue_series = await self._group_series(
            filters,
            timestamp=Task.due_date,
            interval=interval,
            extra_filters=[
                Task.due_date >= start,
                Task.due_date <= end,
                Task.due_date < now,
                Task.completed_at.is_(None),
            ],
        )
        status_breakdown = {item: 0 for item in TaskStatus}
        for item, count in statuses:
            status_breakdown[item] = int(count)
        priority_breakdown = {item: 0 for item in TaskPriority}
        for item, count in priorities:
            priority_breakdown[item] = int(count)
        return TaskAnalyticsRaw(
            total=int(summary[0]),
            completed=int(summary[1]),
            overdue=int(summary[2]),
            average_completion_time_hours=float(summary[3]),
            status_breakdown=status_breakdown,
            priority_breakdown=priority_breakdown,
            created_series=created_series,
            completed_series=completed_series,
            overdue_series=overdue_series,
        )

    async def workload(
        self,
        scope: AnalyticsScope,
        *,
        now: datetime,
        project_id: UUID | None,
        role: str | None,
        active_only: bool,
        sort_by: WorkloadSortField,
        direction: SortDirection,
        limit: int,
        offset: int,
    ) -> tuple[list[WorkloadRow], int]:
        task_scope = self.task_visibility(scope)
        task_conditions: list[ColumnElement[bool]] = [
            task_scope,
            Project.archived_at.is_(None),
        ]
        if project_id is not None:
            task_conditions.append(Task.project_id == project_id)
        eligible = and_(*task_conditions)
        active = func.coalesce(
            func.sum(
                case(
                    (
                        and_(
                            eligible,
                            Task.completed_at.is_(None),
                        ),
                        1,
                    ),
                    else_=0,
                )
            ),
            0,
        ).label("active_task_count")
        overdue = func.coalesce(
            func.sum(
                case(
                    (
                        and_(
                            eligible,
                            Task.due_date < now,
                            Task.completed_at.is_(None),
                        ),
                        1,
                    ),
                    else_=0,
                )
            ),
            0,
        ).label("overdue_task_count")
        completed = func.coalesce(
            func.sum(
                case(
                    (
                        and_(
                            eligible,
                            Task.completed_at.is_not(None),
                        ),
                        1,
                    ),
                    else_=0,
                )
            ),
            0,
        ).label("completed_task_count")
        urgent = func.coalesce(
            func.sum(
                case(
                    (
                        and_(
                            eligible,
                            Task.completed_at.is_(None),
                            Task.priority == TaskPriority.URGENT,
                        ),
                        1,
                    ),
                    else_=0,
                )
            ),
            0,
        ).label("urgent_task_count")
        high = func.coalesce(
            func.sum(
                case(
                    (
                        and_(
                            eligible,
                            Task.completed_at.is_(None),
                            Task.priority == TaskPriority.HIGH,
                        ),
                        1,
                    ),
                    else_=0,
                )
            ),
            0,
        ).label("high_priority_task_count")
        due_7 = func.coalesce(
            func.sum(
                case(
                    (
                        and_(
                            eligible,
                            Task.completed_at.is_(None),
                            Task.due_date >= now,
                            Task.due_date <= now + timedelta(days=7),
                        ),
                        1,
                    ),
                    else_=0,
                )
            ),
            0,
        ).label("due_next_7_days")
        average = func.coalesce(
            func.avg(
                case(
                    (eligible, self._completion_hours()),
                    else_=None,
                )
            ),
            0.0,
        ).label("average_completion_time_hours")
        score = (active + overdue * 3 + urgent * 2 + high + due_7).label(
            "workload_score"
        )
        user_filters: list[ColumnElement[bool]] = []
        if active_only:
            user_filters.append(User.is_active.is_(True))
        if role is not None:
            user_filters.append(
                exists(
                    select(UserRole.user_id)
                    .join(Role, Role.id == UserRole.role_id)
                    .where(
                        UserRole.user_id == User.id,
                        Role.name == role,
                    )
                    .correlate(User)
                )
            )
        if scope.kind is not VisibilityKind.GLOBAL:
            user_filters.append(
                exists(
                    select(Task.id)
                    .join(Project, Project.id == Task.project_id)
                    .where(
                        Task.assignee_id == User.id,
                        *task_conditions,
                    )
                    .correlate(User)
                )
            )
        columns: dict[WorkloadSortField, Any] = {
            WorkloadSortField.EMAIL: User.email,
            WorkloadSortField.ACTIVE_TASK_COUNT: active,
            WorkloadSortField.OVERDUE_TASK_COUNT: overdue,
            WorkloadSortField.COMPLETED_TASK_COUNT: completed,
            WorkloadSortField.WORKLOAD_SCORE: score,
        }
        sort_column = columns[sort_by]
        ordering = (
            sort_column.asc().nulls_last()
            if direction is SortDirection.ASC
            else sort_column.desc().nulls_last()
        )
        id_ordering = (
            User.id.asc()
            if direction is SortDirection.ASC
            else User.id.desc()
        )
        statement = (
            select(
                User.id,
                User.email,
                active,
                overdue,
                completed,
                urgent,
                high,
                due_7,
                average,
            )
            .outerjoin(Task, Task.assignee_id == User.id)
            .outerjoin(Project, Project.id == Task.project_id)
            .where(*user_filters)
            .group_by(User.id, User.email)
            .order_by(ordering, id_ordering)
            .limit(limit)
            .offset(offset)
        )
        rows = await self.session.execute(statement)
        count_statement = select(func.count(User.id)).where(*user_filters)
        total = await self.session.scalar(count_statement)
        return (
            [
                WorkloadRow(
                    user_id=row[0],
                    email=row[1],
                    active_task_count=int(row[2]),
                    overdue_task_count=int(row[3]),
                    completed_task_count=int(row[4]),
                    urgent_task_count=int(row[5]),
                    high_priority_task_count=int(row[6]),
                    due_next_7_days=int(row[7]),
                    average_completion_time_hours=float(row[8]),
                )
                for row in rows
            ],
            int(total or 0),
        )

    @staticmethod
    def project_visibility(
        scope: AnalyticsScope,
    ) -> ColumnElement[bool]:
        if scope.kind is VisibilityKind.GLOBAL:
            return true()
        if scope.kind is VisibilityKind.OWNED:
            return Project.owner_id == scope.user_id
        return Project.id.in_(
            select(Task.project_id).where(
                Task.assignee_id == scope.user_id
            )
        )

    @staticmethod
    def task_visibility(
        scope: AnalyticsScope,
    ) -> ColumnElement[bool]:
        if scope.kind is VisibilityKind.GLOBAL:
            return true()
        if scope.kind is VisibilityKind.OWNED:
            return Project.owner_id == scope.user_id
        return Task.assignee_id == scope.user_id

    def _completion_hours(
        self,
    ) -> ColumnElement[float | Decimal]:
        dialect = self.session.bind.dialect.name if self.session.bind else ""
        if dialect == "sqlite":
            duration = (
                func.julianday(Task.completed_at)
                - func.julianday(Task.created_at)
            ) * 24.0
        else:
            duration = extract(
                "epoch",
                Task.completed_at - Task.created_at,
            ) / 3600.0
        return case(
            (
                Task.completed_at >= Task.created_at,
                duration,
            ),
            else_=None,
        )

    async def _group_series(
        self,
        filters: list[ColumnElement[bool]],
        *,
        timestamp: ColumnExpressionArgument[datetime | None],
        interval: AnalyticsInterval,
        extra_filters: list[ColumnElement[bool]] | None = None,
    ) -> dict[datetime, int]:
        bucket = self._bucket_expression(timestamp, interval)
        rows = await self.session.execute(
            select(bucket, func.count(Task.id))
            .select_from(Task)
            .join(Project, Project.id == Task.project_id)
            .where(*filters, *(extra_filters or []))
            .group_by(bucket)
            .order_by(bucket)
        )
        return {
            self._bucket_datetime(row[0]): int(row[1])
            for row in rows
            if row[0] is not None
        }

    def _bucket_expression(
        self,
        timestamp: ColumnExpressionArgument[datetime | None],
        interval: AnalyticsInterval,
    ) -> ColumnElement[Any]:
        dialect = self.session.bind.dialect.name if self.session.bind else ""
        if dialect != "sqlite":
            return cast(func.date_trunc(interval.value, timestamp), Date)
        if interval is AnalyticsInterval.DAY:
            return func.date(timestamp)
        if interval is AnalyticsInterval.MONTH:
            return func.strftime("%Y-%m-01", timestamp)
        weekday = cast(func.strftime("%w", timestamp), Integer)
        days_since_monday = (weekday + 6) % 7
        modifier = (
            literal("-")
            .concat(cast(days_since_monday, String))
            .concat(literal(" days"))
        )
        return func.date(timestamp, modifier)

    @staticmethod
    def _bucket_datetime(value: date_type | str) -> datetime:
        date_value = (
            value
            if isinstance(value, date_type)
            else date_type.fromisoformat(value)
        )
        return datetime.combine(
            date_value,
            datetime.min.time(),
            tzinfo=timezone.utc,
        )

    async def _active_user_count(
        self,
        scope: AnalyticsScope,
        *,
        task_filters: list[ColumnElement[bool]],
    ) -> int:
        if scope.kind is VisibilityKind.GLOBAL:
            value = await self.session.scalar(
                select(func.count(User.id)).where(
                    User.is_active.is_(True)
                )
            )
            return int(value or 0)
        value = await self.session.scalar(
            select(func.count(func.distinct(User.id)))
            .select_from(User)
            .join(Task, Task.assignee_id == User.id)
            .join(Project, Project.id == Task.project_id)
            .where(User.is_active.is_(True), *task_filters)
        )
        return int(value or 0)

    @staticmethod
    def _project_activity_subquery(
        start: datetime,
        end: datetime,
    ) -> Any:
        return (
            select(
                AuditLog.project_id,
                func.max(AuditLog.timestamp).label("last_activity_at"),
            )
            .where(
                AuditLog.project_id.is_not(None),
                AuditLog.timestamp >= start,
                AuditLog.timestamp <= end,
            )
            .group_by(AuditLog.project_id)
            .subquery()
        )

    def _project_analytics_filters(
        self,
        scope: AnalyticsScope,
        *,
        start: datetime,
        end: datetime,
        status: ProjectStatus | None,
        owner_id: UUID | None,
        include_archived: bool,
        search: str | None,
    ) -> list[ColumnElement[bool]]:
        filters = [
            self.project_visibility(scope),
            Project.created_at >= start,
            Project.created_at <= end,
        ]
        if status is not None:
            filters.append(Project.status == status)
        if owner_id is not None:
            filters.append(Project.owner_id == owner_id)
        if not include_archived:
            filters.append(Project.archived_at.is_(None))
        if search:
            pattern = f"%{escape_like(search.strip())}%"
            filters.append(
                or_(
                    Project.name.ilike(pattern, escape="\\"),
                    Project.description.ilike(pattern, escape="\\"),
                )
            )
        return filters

    def _task_analytics_filters(
        self,
        scope: AnalyticsScope,
        *,
        start: datetime,
        end: datetime,
        project_id: UUID | None,
        assigned_to: UUID | None,
        status: TaskStatus | None,
        priority: TaskPriority | None,
        include_archived: bool,
    ) -> list[ColumnElement[bool]]:
        filters = [
            self.task_visibility(scope),
            Task.created_at >= start,
            Task.created_at <= end,
        ]
        if project_id is not None:
            filters.append(Task.project_id == project_id)
        if assigned_to is not None:
            filters.append(Task.assignee_id == assigned_to)
        if status is not None:
            filters.append(Task.status == status)
        if priority is not None:
            filters.append(Task.priority == priority)
        if not include_archived:
            filters.append(Project.archived_at.is_(None))
        return filters
    Date,
    Integer,
    String,
    cast,
