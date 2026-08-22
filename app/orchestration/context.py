from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    AgentConversation,
    AgentDefinition,
    AgentRun,
    Campaign,
    DraftStatus,
    EmailDraft,
    OutreachApproval,
    ProjectStatus,
    TaskPriority,
    User,
)
from app.database.repositories import ProjectFilters, TaskFilters
from app.orchestration.exceptions import OrchestrationConfigurationError
from app.orchestration.health import HealthContract
from app.orchestration.router import IntentEngine
from app.orchestration.schemas import (
    AgentCandidate,
    ExecutedAction,
    OrchestrationRequest,
)
from app.schemas.common import SortDirection
from app.schemas.project import ProjectSortField
from app.schemas.task import TaskSortField
from app.services.activity import ActivityService
from app.services.agent import ApprovalService
from app.services.background_execution import BackgroundExecutionService
from app.services.notification import NotificationService
from app.services.outreach import OutreachService
from app.services.permissions import can_approve_agent_actions
from app.services.project import ProjectService
from app.services.task import TaskService


@dataclass(frozen=True, slots=True)
class OrchestrationExecutionContext:
    user: User
    agent: AgentDefinition
    conversation: AgentConversation
    run: AgentRun
    request: OrchestrationRequest
    permissions: frozenset[str]
    agent_candidates: tuple[AgentCandidate, ...] = ()
    correlation_id: UUID = field(default_factory=uuid4)
    timezone: str = "UTC"
    locale: str = "en-GB"
    current_timestamp: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    def __post_init__(self) -> None:
        if self.current_timestamp.tzinfo is None:
            raise OrchestrationConfigurationError(
                "Orchestration timestamp must be timezone-aware"
            )
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as error:
            raise OrchestrationConfigurationError(
                "Invalid orchestration timezone"
            ) from error
        if self.conversation.owner_id != self.user.id:
            raise OrchestrationConfigurationError(
                "Conversation does not belong to user"
            )
        if self.run.triggered_by_id != self.user.id:
            raise OrchestrationConfigurationError(
                "Run was not triggered by user"
            )
        if self.run.conversation_id != self.conversation.id:
            raise OrchestrationConfigurationError(
                "Run does not belong to conversation"
            )
        if self.run.agent_id != self.agent.id:
            raise OrchestrationConfigurationError(
                "Run does not belong to agent"
            )
        if self.conversation.agent_id != self.agent.id:
            raise OrchestrationConfigurationError(
                "Conversation does not belong to agent"
            )

    @property
    def execution_deadline(self) -> float | None:
        value = self.request.metadata.get("execution_deadline_monotonic")
        return value if isinstance(value, (int, float)) else None


class BuiltOrchestrationContext(BaseModel):
    model_config = ConfigDict(extra="forbid")

    system_prompt: str
    user_profile: dict[str, str]
    agent_instructions: str
    conversation: list[dict[str, str]]
    memories: list[str]
    tool_results: list[dict[str, object]]
    integration_results: list[dict[str, object]]
    background_results: list[dict[str, object]]
    executive_state: dict[str, object] | None = None
    token_count: int = Field(ge=0)
    token_limit: int = Field(ge=1)


class OrchestrationContextBuilder(HealthContract):
    component_name = "context"

    def __init__(self, session: AsyncSession | None = None) -> None:
        self.session = session

    @classmethod
    def needs_executive_state(cls, request: OrchestrationRequest) -> bool:
        return IntentEngine.is_executive_focus(request)

    async def resolve_executive_state(
        self, context: OrchestrationExecutionContext
    ) -> dict[str, object] | None:
        if not self.needs_executive_state(context.request):
            return None
        if self.session is None:
            raise OrchestrationConfigurationError(
                "Executive state requires an orchestration session"
            )

        now = context.current_timestamp.astimezone(timezone.utc)
        zone = ZoneInfo(context.timezone)
        local_now = now.astimezone(zone)
        day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        tasks = await TaskService(self.session).list(
            TaskFilters(completed=False),
            context.user,
            limit=20,
            offset=0,
            sort_by=TaskSortField.PRIORITY,
            direction=SortDirection.DESC,
        )
        active_projects = await ProjectService(self.session).list(
            ProjectFilters(status=ProjectStatus.ACTIVE),
            context.user,
            limit=10,
            offset=0,
            sort_by=ProjectSortField.UPDATED_AT,
            direction=SortDirection.DESC,
        )
        task_rows = list(tasks.items)
        overdue = [
            task for task in task_rows
            if task.due_date is not None and task.due_date.astimezone(zone) < local_now
        ]
        today = [
            task for task in task_rows
            if task.due_date is not None
            and day_start <= task.due_date.astimezone(zone) < day_end
        ]
        urgent = [
            task for task in task_rows
            if task.priority in {TaskPriority.URGENT, TaskPriority.HIGH}
        ]
        projects = []
        for project in active_projects.items:
            project_tasks = [task for task in task_rows if task.project_id == project.id]
            project_overdue = sum(task in overdue for task in project_tasks)
            project_urgent = sum(
                task.priority is TaskPriority.URGENT for task in project_tasks
            )
            projects.append(
                {
                    "name": project.name,
                    "status": project.status.value,
                    "overdue_task_count": project_overdue,
                    "open_urgent_task_count": project_urgent,
                }
            )
        approvals: list[dict[str, object]] = []
        if can_approve_agent_actions(context.user):
            pending = await ApprovalService(self.session).list_pending(
                context.user, limit=10, offset=0
            )
            approvals.extend(
                {
                    "source": "agent",
                    "action_type": item.action_type,
                    "risk_level": item.risk_level.value,
                    "requested_at": item.requested_at.isoformat(),
                }
                for item in pending.items
            )
        outreach = OutreachService(self.session)
        pending_outreach = await outreach.list_entities(
            OutreachApproval, context.user, search=None, limit=10, offset=0
        )
        approvals.extend(
            {
                "source": "outreach",
                "draft_id": str(item.draft_id),
                "requested_at": item.created_at.isoformat(),
            }
            for item in pending_outreach.items
            if item.status.value == "pending"
        )
        schedules = await BackgroundExecutionService(
            self.session
        ).list_active_for_user(context.user.id, limit=10)
        scheduled_drafts = await outreach.list_entities(
            EmailDraft, context.user, search=None, limit=10, offset=0
        )
        scheduled_campaigns = await outreach.list_entities(
            Campaign, context.user, search=None, limit=10, offset=0
        )
        notifications = await NotificationService(self.session).list(
            context.user, is_read=False, notification_type=None, limit=10, offset=0
        )
        activity = await ActivityService(self.session).list(
            context.user,
            actor_id=None,
            entity=None,
            action=None,
            project_id=None,
            start_date=now - timedelta(days=7),
            end_date=now,
            limit=10,
            offset=0,
        )
        return {
            "today": {"date": day_start.date().isoformat(), "timezone": context.timezone},
            "priorities": [self._task(task) for task in urgent[:10]],
            "tasks": {
                "overdue": [self._task(task) for task in overdue[:10]],
                "due_today": [self._task(task) for task in today[:10]],
            },
            "projects": projects,
            "approvals": approvals[:10],
            "scheduled": [
                {
                    "source": "background",
                    "name": item.job_name,
                    "next_run_at": self._time(item.next_run_at),
                }
                for item in schedules
            ] + [
                {
                    "source": "outreach_draft",
                    "name": item.subject,
                    "scheduled_at": self._time(item.scheduled_at),
                }
                for item in scheduled_drafts.items
                if item.status is DraftStatus.SCHEDULED
            ] + [
                {
                    "source": "campaign",
                    "name": item.name,
                    "scheduled_at": self._time(item.scheduled_at),
                }
                for item in scheduled_campaigns.items
                if item.scheduled_at is not None
            ],
            "notifications": [
                {"type": item.type.value, "title": item.title, "message": item.message}
                for item in notifications.items
            ],
            "activity": [
                {"summary": item.summary, "timestamp": item.timestamp.isoformat()}
                for item in activity.items
            ],
            "growth": [
                {"name": item.name, "status": item.status.value}
                for item in scheduled_campaigns.items
                if item.status.value == "active"
            ],
            "unavailable": {
                "decisions": "No persisted decision record available.",
                "portfolio": "Portfolio state unavailable.",
            },
        }

    def build(
        self,
        context: OrchestrationExecutionContext,
        *,
        memories: list[str],
        actions: list[ExecutedAction] | None = None,
        executive_state: dict[str, object] | None = None,
    ) -> BuiltOrchestrationContext:
        actions = actions or []
        tools = [
            action.output
            for action in actions
            if action.target.value == "tool"
        ]
        integrations = [
            action.output
            for action in actions
            if action.target.value == "integration"
        ]
        background = [
            action.output
            for action in actions
            if action.target.value == "background"
        ]
        conversation = [
            {"role": "user", "content": context.request.content}
        ]
        system_prompt = (
            "Arima Executive OS orchestration. "
            f"Locale={context.locale}; timezone={context.timezone}."
        )
        words = (
            system_prompt
            + " "
            + context.agent.system_instructions
            + " "
            + context.request.content
            + " "
            + " ".join(memories)
        ).split()
        limit = context.request.max_context_tokens
        if len(words) > limit:
            memories = memories[: max(0, limit // 20)]
            words = words[:limit]
        return BuiltOrchestrationContext(
            system_prompt=system_prompt,
            user_profile={
                "id": str(context.user.id),
                "name": (
                    f"{context.user.first_name} "
                    f"{context.user.last_name}"
                ),
                "locale": context.locale,
            },
            agent_instructions=context.agent.system_instructions,
            conversation=conversation,
            memories=memories,
            tool_results=tools,
            integration_results=integrations,
            background_results=background,
            executive_state=executive_state,
            token_count=len(words),
            token_limit=limit,
        )

    @staticmethod
    def _task(task: object) -> dict[str, object]:
        return {
            "title": getattr(task, "title"),
            "priority": getattr(task, "priority").value,
            "status": getattr(task, "status").value,
            "due_date": OrchestrationContextBuilder._time(
                getattr(task, "due_date")
            ),
        }

    @staticmethod
    def _time(value: datetime | None) -> str | None:
        return value.isoformat() if value is not None else None
