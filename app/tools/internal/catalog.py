from __future__ import annotations

from collections import Counter
from datetime import timedelta
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    AgentMemoryScope,
    AgentMemoryType,
    Deal,
    Lead,
    Project,
    Task,
)
from app.database.repositories import ProjectFilters, TaskFilters
from app.database.repositories.crm import DealFilters, LeadFilters
from app.schemas.agent import MemoryCreateRequest
from app.schemas.analytics import (
    AnalyticsInterval,
    ProjectAnalyticsSortField,
)
from app.schemas.common import SortDirection
from app.schemas.crm import CRMSortField, DealResponse, LeadResponse
from app.schemas.project import ProjectResponse, ProjectSortField
from app.schemas.task import TaskResponse, TaskSortField
from app.services.activity import ActivityService
from app.services.agent import MemoryService
from app.services.analytics import AnalyticsService
from app.services.crm import CRMService
from app.services.crm_analytics import CRMAnalyticsService
from app.services.notification import NotificationService
from app.services.permissions import has_full_access, user_roles
from app.services.project import ProjectService
from app.services.task import TaskService
from app.tools.base import InternalToolAdapter
from app.tools.context import ToolExecutionContext
from app.tools.exceptions import ToolExecutionError
from app.tools.internal.common import (
    AnalyticsInput,
    IdentifierInput,
    NotificationInput,
    SearchInput,
)
from app.tools.schemas import (
    ToolCapability,
    ToolCategory,
    ToolPermission,
)


def _dump(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json")


def _project_owner(context: ToolExecutionContext) -> UUID | None:
    roles = user_roles(context.current_user)
    if has_full_access(context.current_user):
        return None
    if roles.intersection({"manager", "viewer"}):
        return context.current_user.id
    return None


class SessionTool(InternalToolAdapter):
    def __init__(self, session: AsyncSession) -> None:
        self.session = session


class ProjectSearchTool(SessionTool):
    name = "project.search"
    description = "Search visible Arima projects."
    category = ToolCategory.PROJECTS
    tool_capabilities = frozenset(
        {ToolCapability.READ, ToolCapability.SEARCH}
    )
    input_model = SearchInput

    async def execute(
        self, payload: BaseModel, context: ToolExecutionContext
    ) -> Any:
        data = SearchInput.model_validate(payload)
        page = await ProjectService(self.session).list(
            ProjectFilters(
                owner_id=_project_owner(context),
                search=data.query,
            ),
            context.current_user,
            limit=data.limit,
            offset=data.offset,
            sort_by=ProjectSortField.UPDATED_AT,
            direction=SortDirection.DESC,
        )
        return {
            "items": [
                _dump(ProjectResponse.model_validate(item))
                for item in page.items
            ],
            "total": page.total,
            "limit": page.limit,
            "offset": page.offset,
        }


class ProjectSummaryTool(SessionTool):
    name = "project.summary"
    description = "Return a project and its task summary."
    category = ToolCategory.PROJECTS
    tool_capabilities = frozenset(
        {ToolCapability.READ, ToolCapability.SUMMARY}
    )
    input_model = IdentifierInput

    async def execute(
        self, payload: BaseModel, context: ToolExecutionContext
    ) -> Any:
        data = IdentifierInput.model_validate(payload)
        project = await ProjectService(self.session).get(
            data.id,
            context.current_user,
        )
        owner = _project_owner(context)
        if owner is not None and project.owner_id != owner:
            raise ToolExecutionError("Project is not visible")
        tasks = await TaskService(self.session).list(
            TaskFilters(project_id=project.id),
            context.current_user,
            limit=100,
            offset=0,
            sort_by=TaskSortField.UPDATED_AT,
            direction=SortDirection.DESC,
        )
        statuses = Counter(item.status.value for item in tasks.items)
        return {
            "project": _dump(ProjectResponse.model_validate(project)),
            "task_count": tasks.total,
            "tasks_by_status": dict(statuses),
        }


class ProjectAnalyticsTool(SessionTool):
    name = "project.analytics"
    description = "Return scoped project performance analytics."
    category = ToolCategory.PROJECTS
    tool_capabilities = frozenset(
        {ToolCapability.READ, ToolCapability.ANALYTICS}
    )
    input_model = AnalyticsInput

    async def execute(
        self, payload: BaseModel, context: ToolExecutionContext
    ) -> Any:
        data = AnalyticsInput.model_validate(payload)
        result = await AnalyticsService(self.session).project_analytics(
            context.current_user,
            start_date=data.start_date,
            end_date=data.end_date,
            status=None,
            owner_id=_project_owner(context),
            include_archived=False,
            search=None,
            sort_by=ProjectAnalyticsSortField.COMPLETION_RATE,
            direction=SortDirection.DESC,
            limit=data.limit,
            offset=data.offset,
        )
        return _dump(result)


class TaskSearchTool(SessionTool):
    name = "task.search"
    description = "Search tasks visible to the current user."
    category = ToolCategory.TASKS
    tool_capabilities = frozenset(
        {ToolCapability.READ, ToolCapability.SEARCH}
    )
    input_model = SearchInput

    async def execute(
        self, payload: BaseModel, context: ToolExecutionContext
    ) -> Any:
        data = SearchInput.model_validate(payload)
        roles = user_roles(context.current_user)
        assigned_to = (
            context.current_user.id
            if not has_full_access(context.current_user)
            and "manager" not in roles
            else None
        )
        page = await TaskService(self.session).list(
            TaskFilters(search=data.query, assigned_to=assigned_to),
            context.current_user,
            limit=data.limit,
            offset=data.offset,
            sort_by=TaskSortField.UPDATED_AT,
            direction=SortDirection.DESC,
        )
        return {
            "items": [
                _dump(TaskResponse.model_validate(item))
                for item in page.items
            ],
            "total": page.total,
            "limit": page.limit,
            "offset": page.offset,
        }


class TaskSummaryTool(SessionTool):
    name = "task.summary"
    description = "Return a visible task summary."
    category = ToolCategory.TASKS
    tool_capabilities = frozenset(
        {ToolCapability.READ, ToolCapability.SUMMARY}
    )
    input_model = IdentifierInput

    async def execute(
        self, payload: BaseModel, context: ToolExecutionContext
    ) -> Any:
        data = IdentifierInput.model_validate(payload)
        task = await TaskService(self.session).get(
            data.id,
            context.current_user,
        )
        roles = user_roles(context.current_user)
        if (
            not has_full_access(context.current_user)
            and "manager" not in roles
            and task.assignee_id != context.current_user.id
        ):
            raise ToolExecutionError("Task is not visible")
        return _dump(TaskResponse.model_validate(task))


class TaskAnalyticsTool(SessionTool):
    name = "task.analytics"
    description = "Return scoped task analytics."
    category = ToolCategory.TASKS
    tool_capabilities = frozenset(
        {ToolCapability.READ, ToolCapability.ANALYTICS}
    )
    input_model = AnalyticsInput

    async def execute(
        self, payload: BaseModel, context: ToolExecutionContext
    ) -> Any:
        data = AnalyticsInput.model_validate(payload)
        result = await AnalyticsService(self.session).task_analytics(
            context.current_user,
            start_date=data.start_date,
            end_date=data.end_date,
            project_id=None,
            assigned_to=None,
            status=None,
            priority=None,
            interval=AnalyticsInterval.DAY,
        )
        return _dump(result)


class LeadSearchTool(SessionTool):
    name = "lead.search"
    description = "Search CRM leads within the current RBAC scope."
    category = ToolCategory.CRM
    tool_capabilities = frozenset(
        {ToolCapability.READ, ToolCapability.SEARCH}
    )
    input_model = SearchInput

    async def execute(
        self, payload: BaseModel, context: ToolExecutionContext
    ) -> Any:
        data = SearchInput.model_validate(payload)
        page = await CRMService(self.session).list_leads(
            context.current_user,
            LeadFilters(search=data.query),
            limit=data.limit,
            offset=data.offset,
            sort_by=CRMSortField.UPDATED_AT,
            direction=SortDirection.DESC,
        )
        return {
            "items": [
                _dump(LeadResponse.model_validate(item))
                for item in page.items
            ],
            "total": page.total,
            "limit": page.limit,
            "offset": page.offset,
        }


class LeadSummaryTool(SessionTool):
    name = "lead.summary"
    description = "Return a scoped CRM lead summary."
    category = ToolCategory.CRM
    tool_capabilities = frozenset(
        {ToolCapability.READ, ToolCapability.SUMMARY}
    )
    input_model = IdentifierInput

    async def execute(
        self, payload: BaseModel, context: ToolExecutionContext
    ) -> Any:
        data = IdentifierInput.model_validate(payload)
        lead = await CRMService(self.session).get_lead(
            data.id, context.current_user
        )
        return _dump(LeadResponse.model_validate(lead))


class OpportunitySearchTool(SessionTool):
    name = "opportunity.search"
    description = "Search internal CRM opportunities."
    category = ToolCategory.CRM
    tool_capabilities = frozenset(
        {ToolCapability.READ, ToolCapability.SEARCH}
    )
    input_model = SearchInput

    async def execute(
        self, payload: BaseModel, context: ToolExecutionContext
    ) -> Any:
        data = SearchInput.model_validate(payload)
        page = await CRMService(self.session).list_deals(
            context.current_user,
            DealFilters(search=data.query),
            limit=data.limit,
            offset=data.offset,
            sort_by=CRMSortField.UPDATED_AT,
            direction=SortDirection.DESC,
        )
        return {
            "items": [
                _dump(DealResponse.model_validate(item))
                for item in page.items
            ],
            "total": page.total,
            "limit": page.limit,
            "offset": page.offset,
        }


class PipelineAnalyticsTool(SessionTool):
    name = "pipeline.analytics"
    description = "Return the scoped CRM pipeline analytics."
    category = ToolCategory.CRM
    tool_capabilities = frozenset(
        {ToolCapability.READ, ToolCapability.ANALYTICS}
    )
    input_model = AnalyticsInput

    async def execute(
        self, payload: BaseModel, context: ToolExecutionContext
    ) -> Any:
        data = AnalyticsInput.model_validate(payload)
        result = await CRMAnalyticsService(self.session).pipeline(
            context.current_user,
            start_date=data.start_date,
            end_date=data.end_date,
        )
        return _dump(result)


class RecentActivityTool(SessionTool):
    name = "activity.recent"
    description = "Return recent scoped platform activity."
    category = ToolCategory.ACTIVITIES
    tool_capabilities = frozenset(
        {ToolCapability.READ, ToolCapability.SEARCH}
    )
    input_model = NotificationInput

    async def execute(
        self, payload: BaseModel, context: ToolExecutionContext
    ) -> Any:
        data = NotificationInput.model_validate(payload)
        result = await ActivityService(self.session).list(
            context.current_user,
            actor_id=None,
            entity=None,
            action=None,
            project_id=None,
            start_date=context.current_timestamp - timedelta(days=30),
            end_date=context.current_timestamp,
            limit=data.limit,
            offset=data.offset,
        )
        return _dump(result)


class ActivitySummaryTool(SessionTool):
    name = "activity.summary"
    description = "Summarize recent activity by entity and action."
    category = ToolCategory.ACTIVITIES
    tool_capabilities = frozenset(
        {ToolCapability.READ, ToolCapability.SUMMARY}
    )
    input_model = AnalyticsInput

    async def execute(
        self, payload: BaseModel, context: ToolExecutionContext
    ) -> Any:
        data = AnalyticsInput.model_validate(payload)
        result = await ActivityService(self.session).list(
            context.current_user,
            actor_id=None,
            entity=None,
            action=None,
            project_id=None,
            start_date=data.start_date,
            end_date=data.end_date,
            limit=100,
            offset=0,
        )
        return {
            "total": result.total,
            "by_entity": dict(
                Counter(item.entity.value for item in result.items)
            ),
            "by_action": dict(
                Counter(item.action.value for item in result.items)
            ),
        }


class UnreadNotificationsTool(SessionTool):
    name = "notification.unread"
    description = "Return unread notifications for the current user."
    category = ToolCategory.NOTIFICATIONS
    tool_capabilities = frozenset(
        {ToolCapability.READ, ToolCapability.SEARCH}
    )
    input_model = NotificationInput

    async def execute(
        self, payload: BaseModel, context: ToolExecutionContext
    ) -> Any:
        data = NotificationInput.model_validate(payload)
        result = await NotificationService(self.session).list(
            context.current_user,
            is_read=False,
            notification_type=None,
            limit=data.limit,
            offset=data.offset,
        )
        return _dump(result)


class NotificationSummaryTool(SessionTool):
    name = "notification.summary"
    description = "Summarize notification volume for the current user."
    category = ToolCategory.NOTIFICATIONS
    tool_capabilities = frozenset(
        {ToolCapability.READ, ToolCapability.SUMMARY}
    )

    async def execute(
        self, payload: BaseModel, context: ToolExecutionContext
    ) -> Any:
        service = NotificationService(self.session)
        unread = await service.unread_count(context.current_user)
        recent = await service.list(
            context.current_user,
            is_read=None,
            notification_type=None,
            limit=100,
            offset=0,
        )
        return {
            "total": recent.total,
            "unread": unread.unread_count,
            "by_type": dict(
                Counter(item.type.value for item in recent.items)
            ),
        }


class MemorySearchInput(SearchInput):
    scope: AgentMemoryScope = AgentMemoryScope.USER


class MemorySearchTool(SessionTool):
    name = "memory.search"
    description = "Search active internal agent memory."
    category = ToolCategory.MEMORY
    tool_capabilities = frozenset(
        {ToolCapability.READ, ToolCapability.SEARCH}
    )
    input_model = MemorySearchInput

    async def execute(
        self, payload: BaseModel, context: ToolExecutionContext
    ) -> Any:
        data = MemorySearchInput.model_validate(payload)
        page = await MemoryService(self.session).search_by_scope(
            scope=data.scope,
            actor=context.current_user,
            agent_id=(
                context.current_agent.id
                if data.scope is AgentMemoryScope.AGENT
                else None
            ),
            conversation_id=(
                context.conversation.id
                if data.scope is AgentMemoryScope.CONVERSATION
                else None
            ),
            key=data.query,
            limit=data.limit,
            offset=data.offset,
        )
        return {
            "items": [
                {
                    "id": str(item.id),
                    "scope": item.scope.value,
                    "type": item.memory_type.value,
                    "key": item.key,
                    "value": item.value,
                    "importance": item.importance,
                }
                for item in page.items
            ],
            "total": page.total,
        }


class MemoryStoreInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=200)
    value: str = Field(min_length=1, max_length=100000)
    memory_type: AgentMemoryType = AgentMemoryType.FACT
    scope: AgentMemoryScope = AgentMemoryScope.CONVERSATION
    importance: int = Field(default=3, ge=1, le=5)


class MemoryStoreTool(SessionTool):
    name = "memory.store"
    description = "Store scoped internal agent memory."
    category = ToolCategory.MEMORY
    permissions = frozenset(
        {ToolPermission.READ, ToolPermission.WRITE, ToolPermission.AUDIT}
    )
    tool_capabilities = frozenset(
        {ToolCapability.WRITE, ToolCapability.SUMMARY}
    )
    input_model = MemoryStoreInput

    async def execute(
        self, payload: BaseModel, context: ToolExecutionContext
    ) -> Any:
        data = MemoryStoreInput.model_validate(payload)
        memory = await MemoryService(self.session).create(
            MemoryCreateRequest(
                agent_id=(
                    context.current_agent.id
                    if data.scope is AgentMemoryScope.AGENT
                    else None
                ),
                conversation_id=(
                    context.conversation.id
                    if data.scope is AgentMemoryScope.CONVERSATION
                    else None
                ),
                memory_type=data.memory_type,
                scope=data.scope,
                key=data.key,
                value=data.value,
                importance=data.importance,
            ),
            context.current_user,
        )
        return {"id": str(memory.id), "stored": True, "key": memory.key}


class MemorySummaryTool(SessionTool):
    name = "memory.summary"
    description = "Summarize active memory for the current conversation."
    category = ToolCategory.MEMORY
    tool_capabilities = frozenset(
        {ToolCapability.READ, ToolCapability.SUMMARY}
    )

    async def execute(
        self, payload: BaseModel, context: ToolExecutionContext
    ) -> Any:
        page = await MemoryService(self.session).search_by_scope(
            scope=AgentMemoryScope.CONVERSATION,
            actor=context.current_user,
            conversation_id=context.conversation.id,
            limit=100,
        )
        return {
            "total": page.total,
            "by_type": dict(
                Counter(item.memory_type.value for item in page.items)
            ),
            "keys": [item.key for item in page.items],
        }


class DashboardTool(SessionTool):
    input_model = AnalyticsInput

    async def dashboard(
        self, payload: BaseModel, context: ToolExecutionContext
    ) -> dict[str, Any]:
        data = AnalyticsInput.model_validate(payload)
        result = await AnalyticsService(self.session).dashboard_summary(
            context.current_user,
            start_date=data.start_date,
            end_date=data.end_date,
            project_id=None,
            owner_id=None,
            assigned_to=None,
            timezone_name=context.timezone,
            include_archived=False,
            refresh=False,
        )
        return _dump(result)


class ExecutiveDashboardTool(DashboardTool):
    name = "dashboard.executive"
    description = "Return the scoped executive dashboard."
    category = ToolCategory.DASHBOARD
    tool_capabilities = frozenset(
        {ToolCapability.READ, ToolCapability.SUMMARY}
    )

    async def execute(
        self, payload: BaseModel, context: ToolExecutionContext
    ) -> Any:
        return await self.dashboard(payload, context)


class AnalyticsDashboardTool(DashboardTool):
    name = "dashboard.analytics"
    description = "Return analytics dashboard data."
    category = ToolCategory.DASHBOARD
    tool_capabilities = frozenset(
        {ToolCapability.READ, ToolCapability.ANALYTICS}
    )

    async def execute(
        self, payload: BaseModel, context: ToolExecutionContext
    ) -> Any:
        return await self.dashboard(payload, context)


class PortfolioSummaryTool(DashboardTool):
    name = "portfolio.summary"
    description = "Combine project and pipeline portfolio indicators."
    category = ToolCategory.PORTFOLIO
    tool_capabilities = frozenset(
        {ToolCapability.READ, ToolCapability.SUMMARY}
    )

    async def execute(
        self, payload: BaseModel, context: ToolExecutionContext
    ) -> Any:
        data = AnalyticsInput.model_validate(payload)
        dashboard = await self.dashboard(payload, context)
        pipeline = await CRMAnalyticsService(self.session).pipeline(
            context.current_user,
            start_date=data.start_date,
            end_date=data.end_date,
        )
        return {"operations": dashboard, "pipeline": _dump(pipeline)}


class PortfolioAnalyticsTool(PortfolioSummaryTool):
    name = "portfolio.analytics"
    description = "Return combined portfolio analytics."
    tool_capabilities = frozenset(
        {ToolCapability.READ, ToolCapability.ANALYTICS}
    )


class PlatformHealthTool(SessionTool):
    name = "platform.health"
    description = "Check internal database and tool platform health."
    category = ToolCategory.HEALTH
    tool_capabilities = frozenset(
        {ToolCapability.READ, ToolCapability.HEALTH}
    )

    async def execute(
        self, payload: BaseModel, context: ToolExecutionContext
    ) -> Any:
        await self.session.execute(text("SELECT 1"))
        return {
            "status": "healthy",
            "database": "available",
            "checked_at": context.current_timestamp.isoformat(),
        }


class SystemStatusTool(SessionTool):
    name = "system.status"
    description = "Return internal platform record status counts."
    category = ToolCategory.HEALTH
    tool_capabilities = frozenset(
        {ToolCapability.READ, ToolCapability.HEALTH}
    )

    async def execute(
        self, payload: BaseModel, context: ToolExecutionContext
    ) -> Any:
        user_id = context.current_user.id
        counts = {
            "projects": int(
                await self.session.scalar(
                    select(func.count()).select_from(Project).where(
                        Project.owner_id == user_id
                    )
                )
                or 0
            ),
            "tasks": int(
                await self.session.scalar(
                    select(func.count())
                    .select_from(Task)
                    .join(Project, Project.id == Task.project_id)
                    .where(Project.owner_id == user_id)
                )
                or 0
            ),
            "leads": int(
                await self.session.scalar(
                    select(func.count()).select_from(Lead).where(
                        or_(Lead.owner_id == user_id, Lead.created_by == user_id)
                    )
                )
                or 0
            ),
            "opportunities": int(
                await self.session.scalar(
                    select(func.count()).select_from(Deal).where(
                        Deal.owner_id == user_id
                    )
                )
                or 0
            ),
        }
        return {
            "status": "operational",
            "counts": counts,
            "timestamp": context.current_timestamp.isoformat(),
        }


INTERNAL_TOOL_TYPES: tuple[type[InternalToolAdapter], ...] = (
    ProjectSearchTool,
    ProjectSummaryTool,
    ProjectAnalyticsTool,
    TaskSearchTool,
    TaskSummaryTool,
    TaskAnalyticsTool,
    LeadSearchTool,
    LeadSummaryTool,
    OpportunitySearchTool,
    PipelineAnalyticsTool,
    RecentActivityTool,
    ActivitySummaryTool,
    UnreadNotificationsTool,
    NotificationSummaryTool,
    MemorySearchTool,
    MemoryStoreTool,
    MemorySummaryTool,
    ExecutiveDashboardTool,
    AnalyticsDashboardTool,
    PortfolioSummaryTool,
    PortfolioAnalyticsTool,
    PlatformHealthTool,
    SystemStatusTool,
)
