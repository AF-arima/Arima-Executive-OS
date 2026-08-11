from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import EmailDraft, User
from app.database.repositories import (
    AgentContextSnapshotRepository,
    AgentConversationRepository,
    AgentMemoryRepository,
    AgentMessageRepository,
    AgentRunRepository,
    CRMRepository,
    CompanyFilters,
    NotificationRepository,
    OutreachRepository,
    ProjectFilters,
    ProjectRepository,
    TaskFilters,
    TaskRepository,
)
from app.execution.exceptions import (
    ContextBuildFailure,
    PromptBuildFailure,
)
from app.execution.types import (
    ExecutionContext,
    PromptMessage,
    StructuredPrompt,
    ToolResult,
)
from app.schemas.common import SortDirection
from app.schemas.crm import CRMSortField
from app.schemas.project import ProjectSortField
from app.schemas.task import TaskSortField
from app.services.permissions import (
    can_view_conversation,
    can_approve_agent_actions,
    can_invoke_agents,
    can_manage_agents,
    can_manage_memory,
    crm_scope,
    has_full_access,
    outreach_scope,
    user_roles,
)

UTC = timezone.utc


class ContextBuilder:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.runs = AgentRunRepository(session)
        self.conversations = AgentConversationRepository(session)
        self.messages = AgentMessageRepository(session)
        self.memories = AgentMemoryRepository(session)
        self.snapshots = AgentContextSnapshotRepository(session)
        self.projects = ProjectRepository(session)
        self.tasks = TaskRepository(session)
        self.crm = CRMRepository(session)
        self.outreach = OutreachRepository(session)
        self.notifications = NotificationRepository(session)

    async def build(self, run_id: UUID, actor: User) -> ExecutionContext:
        try:
            return await self._build(run_id, actor)
        except ContextBuildFailure:
            raise
        except Exception as error:
            raise ContextBuildFailure(
                f"Execution context could not be built: {error}"
            ) from error

    async def _build(self, run_id: UUID, actor: User) -> ExecutionContext:
        run = await self.runs.get(run_id)
        if run is None:
            raise ContextBuildFailure("Run not found")
        conversation = await self.conversations.get(run.conversation_id)
        if conversation is None:
            raise ContextBuildFailure("Conversation not found")
        if not can_view_conversation(actor, conversation):
            raise ContextBuildFailure("Actor cannot access this run")

        message_page = await self.messages.list_for_conversation(
            conversation.id,
            limit=100,
            offset=0,
        )
        memory_pages = [
            await self.memories.list_scoped(
                owner_id=actor.id,
                active_unexpired_at=datetime.now(UTC),
                limit=100,
                offset=0,
            ),
            await self.memories.list_scoped(
                conversation_id=conversation.id,
                active_unexpired_at=datetime.now(UTC),
                limit=100,
                offset=0,
            ),
        ]
        owner_filter = None if has_full_access(actor) else actor.id
        project_page = await self.projects.list_filtered(
            ProjectFilters(owner_id=owner_filter),
            limit=25,
            offset=0,
            sort_by=ProjectSortField.UPDATED_AT,
            direction=SortDirection.DESC,
        )
        task_page = await self.tasks.list_filtered(
            TaskFilters(
                assigned_to=None if has_full_access(actor) else actor.id
            ),
            now=datetime.now(UTC),
            # Assignment is not an ownership grant.  This defensive project
            # predicate keeps inconsistent legacy/imported task rows out of
            # the actor's AI context as well.
            owner_id=None if has_full_access(actor) else actor.id,
            limit=25,
            offset=0,
            sort_by=TaskSortField.UPDATED_AT,
            direction=SortDirection.DESC,
        )
        company_page = await self.crm.list_companies(
            crm_scope(actor),
            CompanyFilters(),
            limit=25,
            offset=0,
            sort_by=CRMSortField.UPDATED_AT,
            direction=SortDirection.DESC,
        )
        outreach_page = await self.outreach.list_visible(
            EmailDraft,
            outreach_scope(actor),
            search=None,
            limit=25,
            offset=0,
        )
        notification_page = await self.notifications.list_for_user(
            actor.id,
            now=datetime.now(UTC),
            is_read=None,
            notification_type=None,
            limit=25,
            offset=0,
        )
        run_page = await self.runs.list_scoped(
            conversation_id=conversation.id,
            limit=20,
            offset=0,
        )

        user_context: dict[str, Any] = {
            "id": str(actor.id),
            "roles": sorted(user_roles(actor)),
            "active": actor.is_active,
        }
        conversation_context: dict[str, Any] = {
            "id": str(conversation.id),
            "agent_id": str(conversation.agent_id),
            "owner_id": str(conversation.owner_id),
            "title": conversation.title,
            "status": conversation.status.value,
            "priority": conversation.priority.value,
        }
        message_context = tuple(
            {
                "id": str(message.id),
                "role": message.role.value,
                "content": message.content,
                "sequence_number": message.sequence_number,
            }
            for message in message_page.items
        )
        seen_memories: set[UUID] = set()
        memory_context: list[dict[str, Any]] = []
        for page in memory_pages:
            for memory in page.items:
                if memory.id in seen_memories:
                    continue
                seen_memories.add(memory.id)
                memory_context.append(
                    {
                        "id": str(memory.id),
                        "scope": memory.scope.value,
                        "type": memory.memory_type.value,
                        "key": memory.key,
                        "value": memory.value,
                        "importance": memory.importance,
                    }
                )
        permission_context: dict[str, Any] = {
            "manage_agents": can_manage_agents(actor),
            "invoke_agents": can_invoke_agents(actor),
            "approve_agent_actions": can_approve_agent_actions(actor),
            "manage_memory": can_manage_memory(actor),
        }
        projects = tuple(
            {
                "id": str(project.id),
                "name": project.name,
                "status": project.status.value,
                "owner_id": str(project.owner_id),
            }
            for project in project_page.items
        )
        tasks = tuple(
            {
                "id": str(task.id),
                "title": task.title,
                "status": task.status.value,
                "project_id": str(task.project_id),
                "assignee_id": (
                    str(task.assignee_id)
                    if task.assignee_id is not None
                    else None
                ),
            }
            for task in task_page.items
        )
        crm_context: dict[str, Any] = {
            "companies": [
                {"id": str(company.id), "name": company.name}
                for company in company_page.items
            ],
            "total": company_page.total,
        }
        outreach_context: dict[str, Any] = {
            "drafts": [
                {
                    "id": str(draft.id),
                    "subject": draft.subject,
                    "status": draft.status.value,
                }
                for draft in outreach_page.items
            ],
            "total": outreach_page.total,
        }
        notifications = tuple(
            {
                "id": str(item.id),
                "type": item.type.value,
                "title": item.title,
                "is_read": item.is_read,
            }
            for item in notification_page.items
        )
        previous_runs = tuple(
            {
                "id": str(item.id),
                "status": item.status.value,
                "total_tokens": item.total_tokens,
            }
            for item in run_page.items
            if item.id != run.id
        )
        snapshot_values = {
            "user_context": user_context,
            "permission_context": permission_context,
            "project_context": {"items": list(projects)},
            "task_context": {"items": list(tasks)},
            "crm_context": crm_context,
            "outreach_context": outreach_context,
            "notification_context": {"items": list(notifications)},
            "memory_context": {"items": memory_context},
        }
        snapshot = await self.snapshots.get_by_run(run.id)
        if snapshot is None:
            snapshot = await self.snapshots.create(
                {"run_id": run.id, **snapshot_values}
            )
        else:
            snapshot = await self.snapshots.update(
                snapshot,
                snapshot_values,
            )
        context = ExecutionContext(
            run_id=run.id,
            snapshot_id=snapshot.id,
            user=user_context,
            conversation=conversation_context,
            messages=message_context,
            memory=tuple(memory_context),
            permissions=permission_context,
            projects=projects,
            tasks=tasks,
            crm=crm_context,
            outreach=outreach_context,
            notifications=notifications,
            previous_runs=previous_runs,
        )
        await self.runs.update(
            run,
            {"context_snapshot": context.as_dict()},
        )
        return context


class PromptBuilder:
    def build(
        self,
        *,
        system_instructions: str,
        context: ExecutionContext,
        tool_outputs: tuple[ToolResult, ...] = (),
    ) -> StructuredPrompt:
        if not system_instructions.strip():
            raise PromptBuildFailure("System instructions are required")
        try:
            conversation = tuple(
                PromptMessage(
                    role=str(item["role"]),
                    content=str(item["content"]),
                )
                for item in context.messages
            )
            memory = tuple(
                str(item["value"])
                for item in context.memory
                if item.get("value")
            )
            outputs = tuple(
                {
                    "execution_id": str(item.execution_id),
                    "slug": item.slug,
                    "output": item.output,
                }
                for item in tool_outputs
            )
            return StructuredPrompt(
                system_instructions=system_instructions.strip(),
                conversation=conversation,
                memory=memory,
                tool_outputs=outputs,
                context=context.as_dict(),
            )
        except Exception as error:
            raise PromptBuildFailure(
                f"Structured prompt could not be built: {error}"
            ) from error
