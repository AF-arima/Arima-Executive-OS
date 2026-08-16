from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    AgentApproval,
    AgentApprovalStatus,
    AgentConversation,
    AgentDefinition,
    AgentMemory,
    AgentMemoryScope,
    AgentMessage,
    AgentRun,
    AgentRunStatus,
    AgentStatus,
    AuditAction,
    AuditEntity,
    ConversationStatus,
    MessageRole,
    User,
)
from app.database.repositories import (
    AgentApprovalRepository,
    AgentConversationRepository,
    AgentDefinitionRepository,
    AgentMemoryRepository,
    AgentMessageRepository,
    AgentRunRepository,
    AgentToolExecutionRepository,
    Page,
    UserRepository,
    WorkspaceRepository,
)
from app.schemas.agent import (
    AgentApprovalFilter,
    AgentCreateRequest,
    AgentDefinitionFilter,
    AgentMemoryFilter,
    AgentPatchRequest,
    AgentRunFilter,
    ApprovalCreateRequest,
    ConversationCreateRequest,
    ConversationRenameRequest,
    MemoryCreateRequest,
    MemoryPatchRequest,
    MessageCreateRequest,
    RunCreateRequest,
    RunTransitionRequest,
)
from app.services.audit import record_audit
from app.services.exceptions import (
    PermissionDeniedError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from app.services.notification import enqueue_agent_notification
from app.services.permissions import (
    can_approve_agent_actions,
    can_invoke_agents,
    can_manage_agents,
    can_manage_memory,
    can_view_conversation,
    has_full_access,
)

UTC = timezone.utc
TERMINAL_RUN_STATUSES = frozenset(
    {
        AgentRunStatus.COMPLETED,
        AgentRunStatus.FAILED,
        AgentRunStatus.CANCELLED,
    }
)
RUN_TRANSITIONS = {
    AgentRunStatus.QUEUED: frozenset(
        {AgentRunStatus.RUNNING, AgentRunStatus.CANCELLED}
    ),
    AgentRunStatus.RUNNING: frozenset(
        {
            AgentRunStatus.WAITING_FOR_APPROVAL,
            AgentRunStatus.COMPLETED,
            AgentRunStatus.FAILED,
            AgentRunStatus.CANCELLED,
        }
    ),
    AgentRunStatus.WAITING_FOR_APPROVAL: frozenset(
        {AgentRunStatus.RUNNING, AgentRunStatus.CANCELLED}
    ),
    AgentRunStatus.COMPLETED: frozenset(),
    AgentRunStatus.FAILED: frozenset(),
    AgentRunStatus.CANCELLED: frozenset(),
}


def _now() -> datetime:
    return datetime.now(UTC)


def _as_aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _audit(
    session: AsyncSession,
    *,
    actor: User,
    action: AuditAction,
    entity_id: UUID,
) -> None:
    record_audit(
        session,
        actor_id=actor.id,
        action=action,
        entity=AuditEntity.AUTOMATION,
        entity_id=entity_id,
    )


class AgentService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.agents = AgentDefinitionRepository(session)

    async def create(
        self,
        data: AgentCreateRequest,
        actor: User,
    ) -> AgentDefinition:
        self._require_manage(actor)
        if await self.agents.get_by_slug(data.slug, include_archived=True):
            raise ResourceConflictError("Agent slug already exists")
        try:
            agent = await self.agents.create(
                {
                    **data.model_dump(),
                    "status": AgentStatus.DRAFT,
                    "version": 1,
                    "is_default": False,
                    "created_by_id": actor.id,
                }
            )
            _audit(
                self.session,
                actor=actor,
                action=AuditAction.CREATE,
                entity_id=agent.id,
            )
            await self.session.commit()
        except IntegrityError as error:
            await self.session.rollback()
            raise ResourceConflictError("Agent slug already exists") from error
        return agent

    async def update(
        self,
        agent_id: UUID,
        data: AgentPatchRequest,
        actor: User,
    ) -> AgentDefinition:
        self._require_manage(actor)
        agent = await self._mutable(agent_id)
        values = data.model_dump(exclude_unset=True)
        if not values:
            await self.session.rollback()
            return agent
        slug = values.get("slug")
        if isinstance(slug, str) and slug != agent.slug:
            existing = await self.agents.get_by_slug(
                slug,
                include_archived=True,
            )
            if existing is not None and existing.id != agent.id:
                raise ResourceConflictError("Agent slug already exists")
        try:
            agent = await self.agents.update(agent, values)
            _audit(
                self.session,
                actor=actor,
                action=AuditAction.UPDATE,
                entity_id=agent.id,
            )
            await self.session.commit()
        except IntegrityError as error:
            await self.session.rollback()
            raise ResourceConflictError("Agent slug already exists") from error
        return agent

    async def archive(
        self,
        agent_id: UUID,
        actor: User,
    ) -> AgentDefinition:
        self._require_manage(actor)
        agent = await self._mutable(agent_id)
        if agent.is_default:
            raise ResourceConflictError("Default agent cannot be archived")
        agent = await self.agents.archive(agent, archived_at=_now())
        _audit(
            self.session,
            actor=actor,
            action=AuditAction.DELETE,
            entity_id=agent.id,
        )
        enqueue_agent_notification(
            self.session,
            user_id=actor.id,
            entity_type="agent",
            entity_id=agent.id,
            title="Agent archived",
            message=f"{agent.name} was archived.",
        )
        await self.session.commit()
        return agent

    async def activate(
        self,
        agent_id: UUID,
        actor: User,
    ) -> AgentDefinition:
        self._require_manage(actor)
        agent = await self._get_locked(agent_id)
        if agent.status is AgentStatus.ARCHIVED or agent.archived_at is not None:
            raise ResourceConflictError("Archived agents cannot be activated")
        agent = await self.agents.update(
            agent,
            {"status": AgentStatus.ACTIVE},
        )
        await self._commit_status(agent, actor)
        return agent

    async def disable(
        self,
        agent_id: UUID,
        actor: User,
    ) -> AgentDefinition:
        self._require_manage(actor)
        agent = await self._mutable(agent_id)
        if agent.is_default:
            raise ResourceConflictError("Default agent cannot be disabled")
        agent = await self.agents.update(
            agent,
            {"status": AgentStatus.DISABLED},
        )
        await self._commit_status(agent, actor)
        return agent

    async def set_default(
        self,
        agent_id: UUID,
        actor: User,
    ) -> AgentDefinition:
        self._require_manage(actor)
        candidate = await self._get_locked(agent_id)
        if (
            candidate.status is AgentStatus.ARCHIVED
            or candidate.archived_at is not None
        ):
            raise ResourceConflictError(
                "Archived agents cannot become default"
            )
        agent = await self.agents.set_active_default(agent_id)
        if agent is None:
            raise ResourceNotFoundError("Agent not found")
        await self._commit_status(agent, actor)
        return agent

    async def list(
        self,
        filters: AgentDefinitionFilter,
    ) -> Page[AgentDefinition]:
        return await self.agents.list_scoped(
            status=filters.status,
            is_default=filters.is_default,
            include_archived=filters.include_archived,
            limit=filters.limit,
            offset=filters.offset,
        )

    async def get(self, agent_id: UUID) -> AgentDefinition:
        agent = await self.agents.get(agent_id)
        if agent is None:
            raise ResourceNotFoundError("Agent not found")
        return agent

    async def get_default(self) -> AgentDefinition:
        agent = await self.agents.get_active_default()
        if agent is None:
            raise ResourceNotFoundError("Default agent not found")
        return agent

    async def _mutable(self, agent_id: UUID) -> AgentDefinition:
        agent = await self._get_locked(agent_id)
        if agent.status is AgentStatus.ARCHIVED or agent.archived_at is not None:
            raise ResourceConflictError("Archived agents are read-only")
        return agent

    async def _get_locked(self, agent_id: UUID) -> AgentDefinition:
        agent = await self.agents.get_for_update(agent_id)
        if agent is None:
            raise ResourceNotFoundError("Agent not found")
        return agent

    async def _commit_status(
        self,
        agent: AgentDefinition,
        actor: User,
    ) -> None:
        _audit(
            self.session,
            actor=actor,
            action=AuditAction.STATUS_CHANGE,
            entity_id=agent.id,
        )
        await self.session.commit()

    @staticmethod
    def _require_manage(actor: User) -> None:
        if not can_manage_agents(actor):
            raise PermissionDeniedError


class ConversationService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.conversations = AgentConversationRepository(session)
        self.agents = AgentDefinitionRepository(session)
        self.users = UserRepository(session)

    async def create(
        self,
        data: ConversationCreateRequest,
        actor: User,
    ) -> AgentConversation:
        if not can_invoke_agents(actor):
            raise PermissionDeniedError
        owner_id = data.owner_id or actor.id
        if owner_id != actor.id and not has_full_access(actor):
            raise PermissionDeniedError
        owner = await self.users.get_with_roles(owner_id)
        if owner is None or not owner.is_active:
            raise ResourceNotFoundError("User not found")
        workspace = await WorkspaceRepository(self.session).get_by_owner(owner_id)
        if workspace is None:
            raise ResourceNotFoundError("Workspace not found")
        agent = await self.agents.get(data.agent_id)
        if (
            agent is None
            or agent.status is not AgentStatus.ACTIVE
            or agent.archived_at is not None
        ):
            raise ResourceConflictError("An active agent is required")
        metadata = dict(data.metadata)
        metadata["workspace_id"] = str(workspace.id)
        conversation = await self.conversations.create(
            {
                "agent_id": data.agent_id,
                "owner_id": owner_id,
                "title": data.title,
                "priority": data.priority,
                "metadata": metadata,
                "status": ConversationStatus.ACTIVE,
                "pinned": False,
            }
        )
        _audit(
            self.session,
            actor=actor,
            action=AuditAction.CREATE,
            entity_id=conversation.id,
        )
        await self.session.commit()
        return conversation

    async def rename(
        self,
        conversation_id: UUID,
        data: ConversationRenameRequest,
        actor: User,
    ) -> AgentConversation:
        return await self._update_owned(
            conversation_id,
            {"title": data.title},
            actor,
            action=AuditAction.UPDATE,
        )

    async def archive(
        self,
        conversation_id: UUID,
        actor: User,
    ) -> AgentConversation:
        conversation = await self._get_mutable_owned(conversation_id, actor)
        conversation = await self.conversations.archive(
            conversation,
            archived_at=_now(),
        )
        await self._commit(conversation.id, actor, AuditAction.DELETE)
        return conversation

    async def close(
        self,
        conversation_id: UUID,
        actor: User,
    ) -> AgentConversation:
        return await self._update_owned(
            conversation_id,
            {"status": ConversationStatus.CLOSED},
            actor,
            action=AuditAction.STATUS_CHANGE,
        )

    async def pin(
        self,
        conversation_id: UUID,
        actor: User,
    ) -> AgentConversation:
        return await self._update_owned(
            conversation_id,
            {"pinned": True},
            actor,
            action=AuditAction.UPDATE,
        )

    async def unpin(
        self,
        conversation_id: UUID,
        actor: User,
    ) -> AgentConversation:
        return await self._update_owned(
            conversation_id,
            {"pinned": False},
            actor,
            action=AuditAction.UPDATE,
        )

    async def list_user_conversations(
        self,
        actor: User,
        *,
        owner_id: UUID | None,
        agent_id: UUID | None,
        status: ConversationStatus | None,
        pinned: bool | None,
        include_archived: bool,
        limit: int,
        offset: int,
    ) -> Page[AgentConversation]:
        if not has_full_access(actor):
            if owner_id is not None and owner_id != actor.id:
                raise PermissionDeniedError
            owner_id = actor.id
        return await self.conversations.list_scoped(
            owner_id=owner_id,
            agent_id=agent_id,
            status=status,
            pinned=pinned,
            include_archived=include_archived,
            limit=limit,
            offset=offset,
        )

    async def get(
        self,
        conversation_id: UUID,
        actor: User,
    ) -> AgentConversation:
        conversation = await self.conversations.get(conversation_id)
        if conversation is None:
            raise ResourceNotFoundError("Conversation not found")
        if not can_view_conversation(actor, conversation):
            raise PermissionDeniedError
        return conversation

    async def _update_owned(
        self,
        conversation_id: UUID,
        values: dict[str, object],
        actor: User,
        *,
        action: AuditAction,
    ) -> AgentConversation:
        conversation = await self._get_mutable_owned(conversation_id, actor)
        conversation = await self.conversations.update(conversation, values)
        await self._commit(conversation.id, actor, action)
        return conversation

    async def _get_mutable_owned(
        self,
        conversation_id: UUID,
        actor: User,
    ) -> AgentConversation:
        conversation = await self.conversations.get_for_update(conversation_id)
        if conversation is None:
            raise ResourceNotFoundError("Conversation not found")
        if not can_view_conversation(actor, conversation):
            raise PermissionDeniedError
        if (
            conversation.status is ConversationStatus.ARCHIVED
            or conversation.archived_at is not None
        ):
            raise ResourceConflictError("Archived conversations are read-only")
        return conversation

    async def _commit(
        self,
        entity_id: UUID,
        actor: User,
        action: AuditAction,
    ) -> None:
        _audit(
            self.session,
            actor=actor,
            action=action,
            entity_id=entity_id,
        )
        await self.session.commit()


class MessageService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.messages = AgentMessageRepository(session)
        self.conversations = AgentConversationRepository(session)
        self.runs = AgentRunRepository(session)

    async def create_user_message(
        self,
        data: MessageCreateRequest,
        actor: User,
    ) -> AgentMessage:
        if not can_invoke_agents(actor):
            raise PermissionDeniedError
        return await self._create(data, actor, MessageRole.USER)

    async def create_assistant_message(
        self,
        data: MessageCreateRequest,
        actor: User,
    ) -> AgentMessage:
        if not can_manage_agents(actor):
            raise PermissionDeniedError
        return await self._create(data, actor, MessageRole.ASSISTANT)

    async def create_tool_message(
        self,
        data: MessageCreateRequest,
        actor: User,
    ) -> AgentMessage:
        if not can_manage_agents(actor):
            raise PermissionDeniedError
        return await self._create(data, actor, MessageRole.TOOL)

    async def create_approval_message(
        self,
        data: MessageCreateRequest,
        actor: User,
    ) -> AgentMessage:
        if not can_approve_agent_actions(actor):
            raise PermissionDeniedError
        return await self._create(data, actor, MessageRole.APPROVAL)

    async def get_conversation_messages(
        self,
        conversation_id: UUID,
        actor: User,
        *,
        limit: int,
        offset: int,
    ) -> Page[AgentMessage]:
        await self._owned_conversation(conversation_id, actor, lock=False)
        return await self.messages.list_for_conversation(
            conversation_id,
            limit=limit,
            offset=offset,
        )

    async def _create(
        self,
        data: MessageCreateRequest,
        actor: User,
        role: MessageRole,
    ) -> AgentMessage:
        if data.role is not role:
            raise ResourceConflictError("Message role does not match operation")
        conversation = await self._owned_conversation(
            data.conversation_id,
            actor,
            lock=True,
        )
        if data.parent_message_id is not None:
            parent = await self.messages.get(data.parent_message_id)
            if (
                parent is None
                or parent.conversation_id != conversation.id
            ):
                raise ResourceConflictError(
                    "Parent message must belong to the conversation"
                )
        if data.run_id is not None:
            run = await self.runs.get(data.run_id)
            if run is None or run.conversation_id != conversation.id:
                raise ResourceConflictError(
                    "Run must belong to the conversation"
                )
        created_at = _now()
        message = await self.messages.create_sequenced(
            conversation,
            {
                "run_id": data.run_id,
                "parent_message_id": data.parent_message_id,
                "role": role,
                "content": data.content,
                "content_type": data.content_type,
                "token_count": data.token_count,
                "metadata": data.metadata,
                "created_by_id": actor.id,
            },
            created_at=created_at,
        )
        _audit(
            self.session,
            actor=actor,
            action=AuditAction.CREATE,
            entity_id=message.id,
        )
        await self.session.commit()
        return message

    async def _owned_conversation(
        self,
        conversation_id: UUID,
        actor: User,
        *,
        lock: bool,
    ) -> AgentConversation:
        conversation = (
            await self.conversations.get_for_update(conversation_id)
            if lock
            else await self.conversations.get(conversation_id)
        )
        if conversation is None:
            raise ResourceNotFoundError("Conversation not found")
        if not can_view_conversation(actor, conversation):
            raise PermissionDeniedError
        if lock and conversation.status is not ConversationStatus.ACTIVE:
            raise ResourceConflictError(
                "Messages can only be added to active conversations"
            )
        return conversation


class RunService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.runs = AgentRunRepository(session)
        self.conversations = AgentConversationRepository(session)
        self.messages = AgentMessageRepository(session)
        self.agents = AgentDefinitionRepository(session)

    async def create(
        self,
        data: RunCreateRequest,
        actor: User,
    ) -> AgentRun:
        if not can_invoke_agents(actor):
            raise PermissionDeniedError
        conversation = await self.conversations.get(data.conversation_id)
        if conversation is None:
            raise ResourceNotFoundError("Conversation not found")
        if not can_view_conversation(actor, conversation):
            raise PermissionDeniedError
        if conversation.status is not ConversationStatus.ACTIVE:
            raise ResourceConflictError(
                "Runs require an active conversation"
            )
        agent = await self.agents.get(conversation.agent_id)
        if (
            agent is None
            or agent.status is not AgentStatus.ACTIVE
            or agent.archived_at is not None
        ):
            raise ResourceConflictError("Runs require an active agent")
        if data.input_message_id is not None:
            message = await self.messages.get(data.input_message_id)
            if (
                message is None
                or message.conversation_id != conversation.id
            ):
                raise ResourceConflictError(
                    "Input message must belong to the conversation"
                )
        total_tokens = self._total_tokens(
            data.prompt_tokens,
            data.completion_tokens,
        )
        run = await self.runs.create(
            {
                "conversation_id": conversation.id,
                "agent_id": conversation.agent_id,
                "triggered_by_id": actor.id,
                "status": AgentRunStatus.QUEUED,
                "input_message_id": data.input_message_id,
                "prompt_tokens": data.prompt_tokens,
                "completion_tokens": data.completion_tokens,
                "total_tokens": total_tokens,
                "estimated_cost_gbp": data.estimated_cost_gbp,
                "context_snapshot": data.context_snapshot,
                "metadata": data.metadata,
            }
        )
        _audit(
            self.session,
            actor=actor,
            action=AuditAction.CREATE,
            entity_id=run.id,
        )
        await self.session.commit()
        return run

    async def transition(
        self,
        run_id: UUID,
        data: RunTransitionRequest,
        actor: User,
    ) -> AgentRun:
        if not can_invoke_agents(actor):
            raise PermissionDeniedError
        run = await self.runs.get_for_update(run_id)
        if run is None:
            raise ResourceNotFoundError("Run not found")
        conversation = await self.conversations.get(run.conversation_id)
        if conversation is None:
            raise ResourceNotFoundError("Conversation not found")
        if not can_view_conversation(actor, conversation):
            raise PermissionDeniedError
        if data.status not in RUN_TRANSITIONS[run.status]:
            raise ResourceConflictError(
                f"Illegal run transition: {run.status.value} "
                f"to {data.status.value}"
            )
        if data.output_message_id is not None:
            output = await self.messages.get(data.output_message_id)
            if (
                output is None
                or output.conversation_id != conversation.id
            ):
                raise ResourceConflictError(
                    "Output message must belong to the conversation"
                )
        now = _now()
        values = data.model_dump(exclude_unset=True)
        values["status"] = data.status
        if run.status is AgentRunStatus.QUEUED:
            values["started_at"] = now
        if data.status in TERMINAL_RUN_STATUSES:
            values["completed_at"] = now
            started_at = run.started_at or now
            values["latency_ms"] = max(
                0,
                int((now - _as_aware(started_at)).total_seconds() * 1000),
            )
        prompt_tokens = values.get("prompt_tokens", run.prompt_tokens)
        completion_tokens = values.get(
            "completion_tokens",
            run.completion_tokens,
        )
        if not isinstance(prompt_tokens, int):
            prompt_tokens = None
        if not isinstance(completion_tokens, int):
            completion_tokens = None
        values["total_tokens"] = self._total_tokens(
            prompt_tokens,
            completion_tokens,
        )
        cost = values.get("estimated_cost_gbp")
        if isinstance(cost, Decimal):
            values["estimated_cost_gbp"] = cost.quantize(
                Decimal("0.000001")
            )
        if data.status is not AgentRunStatus.FAILED:
            values["failure_code"] = None
            values["failure_message"] = None
        run = await self.runs.update(run, values)
        action = (
            AuditAction.COMPLETE
            if data.status is AgentRunStatus.COMPLETED
            else AuditAction.STATUS_CHANGE
        )
        _audit(
            self.session,
            actor=actor,
            action=action,
            entity_id=run.id,
        )
        if data.status in {
            AgentRunStatus.COMPLETED,
            AgentRunStatus.FAILED,
        }:
            enqueue_agent_notification(
                self.session,
                user_id=conversation.owner_id,
                entity_type="agent_run",
                entity_id=run.id,
                title=(
                    "Agent run completed"
                    if data.status is AgentRunStatus.COMPLETED
                    else "Agent run failed"
                ),
                message=(
                    "An agent run completed successfully."
                    if data.status is AgentRunStatus.COMPLETED
                    else "An agent run failed."
                ),
            )
        await self.session.commit()
        return run

    async def start(self, run_id: UUID, actor: User) -> AgentRun:
        return await self.transition(
            run_id,
            RunTransitionRequest(status=AgentRunStatus.RUNNING),
            actor,
        )

    async def wait_for_approval(
        self,
        run_id: UUID,
        actor: User,
    ) -> AgentRun:
        return await self.transition(
            run_id,
            RunTransitionRequest(
                status=AgentRunStatus.WAITING_FOR_APPROVAL
            ),
            actor,
        )

    async def resume(self, run_id: UUID, actor: User) -> AgentRun:
        return await self.start(run_id, actor)

    async def complete(
        self,
        run_id: UUID,
        data: RunTransitionRequest,
        actor: User,
    ) -> AgentRun:
        if data.status is not AgentRunStatus.COMPLETED:
            raise ResourceConflictError("Completed status is required")
        return await self.transition(run_id, data, actor)

    async def fail(
        self,
        run_id: UUID,
        data: RunTransitionRequest,
        actor: User,
    ) -> AgentRun:
        if data.status is not AgentRunStatus.FAILED:
            raise ResourceConflictError("Failed status is required")
        return await self.transition(run_id, data, actor)

    async def cancel(
        self,
        run_id: UUID,
        actor: User,
    ) -> AgentRun:
        return await self.transition(
            run_id,
            RunTransitionRequest(status=AgentRunStatus.CANCELLED),
            actor,
        )

    async def get(self, run_id: UUID, actor: User) -> AgentRun:
        run = await self.runs.get_with_related(run_id)
        if run is None:
            raise ResourceNotFoundError("Run not found")
        conversation = await self.conversations.get(run.conversation_id)
        if conversation is None:
            raise ResourceNotFoundError("Conversation not found")
        if not can_view_conversation(actor, conversation):
            raise PermissionDeniedError
        return run

    async def list(
        self,
        filters: AgentRunFilter,
        actor: User,
    ) -> Page[AgentRun]:
        triggered_by_id = filters.triggered_by_id
        if triggered_by_id is not None and triggered_by_id != actor.id:
            raise PermissionDeniedError
        return await self.runs.list_scoped(
            owner_id=actor.id,
            conversation_id=filters.conversation_id,
            agent_id=filters.agent_id,
            triggered_by_id=triggered_by_id,
            status=filters.status,
            limit=filters.limit,
            offset=filters.offset,
        )

    @staticmethod
    def _total_tokens(
        prompt_tokens: int | None,
        completion_tokens: int | None,
    ) -> int | None:
        if prompt_tokens is None and completion_tokens is None:
            return None
        return (prompt_tokens or 0) + (completion_tokens or 0)


class ApprovalService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.approvals = AgentApprovalRepository(session)
        self.runs = AgentRunRepository(session)
        self.conversations = AgentConversationRepository(session)
        self.executions = AgentToolExecutionRepository(session)

    async def create(
        self,
        data: ApprovalCreateRequest,
        actor: User,
    ) -> AgentApproval:
        if not can_invoke_agents(actor):
            raise PermissionDeniedError
        run, conversation = await self._run_and_conversation(
            data.run_id,
            actor,
        )
        now = _now()
        if data.expires_at is not None and _as_aware(data.expires_at) <= now:
            raise ResourceConflictError(
                "Approval expiration must be in the future"
            )
        if data.tool_execution_id is not None:
            execution = await self.executions.get(data.tool_execution_id)
            if execution is None or execution.run_id != run.id:
                raise ResourceNotFoundError("Tool execution not found")
        approval = await self.approvals.create(
            {
                **data.model_dump(),
                "requested_by_id": actor.id,
                "status": AgentApprovalStatus.PENDING,
                "requested_at": now,
            }
        )
        _audit(
            self.session,
            actor=actor,
            action=AuditAction.CREATE,
            entity_id=approval.id,
        )
        enqueue_agent_notification(
            self.session,
            user_id=conversation.owner_id,
            entity_type="agent_approval",
            entity_id=approval.id,
            title="Agent approval requested",
            message=f"Approval requested for {approval.action_type}.",
        )
        await self.session.commit()
        del run
        return approval

    async def approve(
        self,
        approval_id: UUID,
        actor: User,
        *,
        decision_note: str | None = None,
    ) -> AgentApproval:
        return await self._decide(
            approval_id,
            actor,
            AgentApprovalStatus.APPROVED,
            decision_note,
        )

    async def reject(
        self,
        approval_id: UUID,
        actor: User,
        *,
        decision_note: str | None = None,
    ) -> AgentApproval:
        return await self._decide(
            approval_id,
            actor,
            AgentApprovalStatus.REJECTED,
            decision_note,
        )

    async def cancel(
        self,
        approval_id: UUID,
        actor: User,
        *,
        decision_note: str | None = None,
    ) -> AgentApproval:
        return await self._decide(
            approval_id,
            actor,
            AgentApprovalStatus.CANCELLED,
            decision_note,
            require_approver=False,
        )

    async def expire(
        self,
        approval_id: UUID,
        actor: User,
    ) -> AgentApproval:
        if not can_approve_agent_actions(actor):
            raise PermissionDeniedError
        approval = await self._pending_locked(approval_id, actor)
        if (
            approval.expires_at is None
            or _as_aware(approval.expires_at) > _now()
        ):
            raise ResourceConflictError("Approval has not expired")
        return await self._finish(
            approval,
            actor,
            AgentApprovalStatus.EXPIRED,
            None,
        )

    async def list_pending(
        self,
        actor: User,
        *,
        limit: int,
        offset: int,
    ) -> Page[AgentApproval]:
        if not can_approve_agent_actions(actor):
            raise PermissionDeniedError
        return await self.approvals.list_pending_unexpired(
            owner_id=actor.id,
            now=_now(),
            limit=limit,
            offset=offset,
        )

    async def get(self, approval_id: UUID, actor: User) -> AgentApproval:
        approval = await self.approvals.get_for_owner(
            approval_id,
            owner_id=actor.id,
        )
        if approval is None:
            raise ResourceNotFoundError("Approval not found")
        return approval

    async def list(
        self,
        filters: AgentApprovalFilter,
        actor: User,
    ) -> Page[AgentApproval]:
        if not can_approve_agent_actions(actor):
            raise PermissionDeniedError
        if (
            filters.requested_by_id is not None
            and filters.requested_by_id != actor.id
        ) or (
            filters.decided_by_id is not None
            and filters.decided_by_id != actor.id
        ):
            raise PermissionDeniedError
        return await self.approvals.list_scoped(
            owner_id=actor.id,
            run_id=filters.run_id,
            status=filters.status,
            requested_by_id=filters.requested_by_id,
            decided_by_id=filters.decided_by_id,
            unexpired_at=_now() if filters.unexpired_only else None,
            limit=filters.limit,
            offset=filters.offset,
        )

    async def _decide(
        self,
        approval_id: UUID,
        actor: User,
        status: AgentApprovalStatus,
        decision_note: str | None,
        *,
        require_approver: bool = True,
    ) -> AgentApproval:
        approval = await self._pending_locked(approval_id, actor)
        if require_approver and not can_approve_agent_actions(actor):
            raise PermissionDeniedError
        if not require_approver and (
            approval.requested_by_id != actor.id
            and not can_approve_agent_actions(actor)
        ):
            raise PermissionDeniedError
        if (
            approval.expires_at is not None
            and _as_aware(approval.expires_at) <= _now()
        ):
            raise ResourceConflictError("Approval has expired")
        return await self._finish(
            approval,
            actor,
            status,
            decision_note,
        )

    async def _finish(
        self,
        approval: AgentApproval,
        actor: User,
        status: AgentApprovalStatus,
        decision_note: str | None,
    ) -> AgentApproval:
        approval = await self.approvals.update(
            approval,
            {
                "status": status,
                "decided_by_id": actor.id,
                "decision_note": decision_note,
                "decided_at": _now(),
            },
        )
        _audit(
            self.session,
            actor=actor,
            action=AuditAction.STATUS_CHANGE,
            entity_id=approval.id,
        )
        if status in {
            AgentApprovalStatus.APPROVED,
            AgentApprovalStatus.REJECTED,
        }:
            enqueue_agent_notification(
                self.session,
                user_id=approval.requested_by_id,
                entity_type="agent_approval",
                entity_id=approval.id,
                title=f"Agent approval {status.value}",
                message=f"Your agent approval was {status.value}.",
            )
        await self.session.commit()
        return approval

    async def _pending_locked(
        self,
        approval_id: UUID,
        actor: User,
    ) -> AgentApproval:
        approval = await self.approvals.get_for_owner(
            approval_id,
            owner_id=actor.id,
            for_update=True,
        )
        if approval is None:
            raise ResourceNotFoundError("Approval not found")
        if approval.status is not AgentApprovalStatus.PENDING:
            raise ResourceConflictError("Approval is already completed")
        return approval

    async def _run_and_conversation(
        self,
        run_id: UUID,
        actor: User,
    ) -> tuple[AgentRun, AgentConversation]:
        run = await self.runs.get(run_id)
        if run is None:
            raise ResourceNotFoundError("Run not found")
        conversation = await self.conversations.get(run.conversation_id)
        if conversation is None:
            raise ResourceNotFoundError("Conversation not found")
        if not can_view_conversation(actor, conversation):
            raise PermissionDeniedError
        return run, conversation


class MemoryService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.memories = AgentMemoryRepository(session)
        self.conversations = AgentConversationRepository(session)
        self.agents = AgentDefinitionRepository(session)
        self.messages = AgentMessageRepository(session)
        self.users = UserRepository(session)

    async def create(
        self,
        data: MemoryCreateRequest,
        actor: User,
    ) -> AgentMemory:
        if not can_manage_memory(actor):
            raise PermissionDeniedError
        values = await self._validated_values(data, actor)
        try:
            memory = await self.memories.create(values)
        except ValueError as error:
            raise ResourceConflictError(str(error)) from error
        _audit(
            self.session,
            actor=actor,
            action=AuditAction.CREATE,
            entity_id=memory.id,
        )
        await self.session.commit()
        return memory

    async def update(
        self,
        memory_id: UUID,
        data: MemoryPatchRequest,
        actor: User,
    ) -> AgentMemory:
        memory = await self._owned_locked(memory_id, actor)
        values = data.model_dump(exclude_unset=True)
        if not values:
            await self.session.rollback()
            return memory
        expires_at = values.get("expires_at")
        if isinstance(expires_at, datetime) and _as_aware(expires_at) <= _now():
            raise ResourceConflictError(
                "Memory expiration must be in the future"
            )
        memory = await self.memories.update(memory, values)
        await self._commit(memory.id, actor, AuditAction.UPDATE)
        return memory

    async def disable(
        self,
        memory_id: UUID,
        actor: User,
    ) -> AgentMemory:
        memory = await self._owned_locked(memory_id, actor)
        memory = await self.memories.soft_disable(memory)
        await self._commit(memory.id, actor, AuditAction.STATUS_CHANGE)
        return memory

    async def delete(self, memory_id: UUID, actor: User) -> None:
        memory = await self._owned_locked(memory_id, actor)
        entity_id = memory.id
        await self.memories.delete(memory)
        await self._commit(entity_id, actor, AuditAction.DELETE)

    async def list_active(
        self,
        filters: AgentMemoryFilter,
        actor: User,
    ) -> Page[AgentMemory]:
        owner_id = filters.owner_id
        if not has_full_access(actor):
            if owner_id is not None and owner_id != actor.id:
                raise PermissionDeniedError
            owner_id = actor.id
        await self._validate_search_scope(
            filters.conversation_id,
            filters.agent_id,
            actor,
        )
        return await self.memories.list_scoped(
            owner_id=owner_id,
            agent_id=filters.agent_id,
            conversation_id=filters.conversation_id,
            memory_type=filters.memory_type,
            scope=filters.scope,
            key=filters.key,
            active_unexpired_at=(
                _now() if filters.active_unexpired_only else None
            ),
            limit=filters.limit,
            offset=filters.offset,
        )

    async def search_by_scope(
        self,
        *,
        scope: AgentMemoryScope,
        actor: User,
        owner_id: UUID | None = None,
        agent_id: UUID | None = None,
        conversation_id: UUID | None = None,
        key: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Page[AgentMemory]:
        if not has_full_access(actor):
            if owner_id is not None and owner_id != actor.id:
                raise PermissionDeniedError
            owner_id = actor.id
        await self._validate_search_scope(conversation_id, agent_id, actor)
        return await self.memories.list_active_unexpired(
            scope=scope,
            now=_now(),
            owner_id=owner_id,
            agent_id=agent_id,
            conversation_id=conversation_id,
            key=key,
            limit=limit,
            offset=offset,
        )

    async def get(self, memory_id: UUID, actor: User) -> AgentMemory:
        memory = await self.memories.get(memory_id)
        if memory is None:
            raise ResourceNotFoundError("Memory not found")
        if not can_manage_memory(actor, memory):
            raise PermissionDeniedError
        if not memory.is_active or (
            memory.expires_at is not None
            and _as_aware(memory.expires_at) <= _now()
        ):
            raise ResourceNotFoundError("Memory not found")
        return memory

    async def _validated_values(
        self,
        data: MemoryCreateRequest,
        actor: User,
    ) -> dict[str, object]:
        values: dict[str, object] = data.model_dump()
        if data.expires_at is not None and _as_aware(data.expires_at) <= _now():
            raise ResourceConflictError(
                "Memory expiration must be in the future"
            )
        if data.scope is AgentMemoryScope.ORGANISATION:
            if not has_full_access(actor):
                raise PermissionDeniedError
            values["owner_id"] = None
        else:
            owner_id = data.owner_id or actor.id
            if owner_id != actor.id and not has_full_access(actor):
                raise PermissionDeniedError
            if await self.users.get_with_roles(owner_id) is None:
                raise ResourceNotFoundError("User not found")
            values["owner_id"] = owner_id
        if data.scope is AgentMemoryScope.AGENT:
            if data.agent_id is None or await self.agents.get(
                data.agent_id
            ) is None:
                raise ResourceNotFoundError("Agent not found")
        if data.scope is AgentMemoryScope.CONVERSATION:
            if data.conversation_id is None:
                raise ResourceConflictError(
                    "Conversation scope requires conversation_id"
                )
            conversation = await self.conversations.get(data.conversation_id)
            if conversation is None:
                raise ResourceNotFoundError("Conversation not found")
            if not can_view_conversation(actor, conversation):
                raise PermissionDeniedError
        if data.source_message_id is not None:
            message = await self.messages.get(data.source_message_id)
            if message is None:
                raise ResourceNotFoundError("Message not found")
            message_conversation = await self.conversations.get(
                message.conversation_id
            )
            if (
                message_conversation is None
                or not can_view_conversation(actor, message_conversation)
            ):
                raise PermissionDeniedError
            if (
                data.conversation_id is not None
                and message.conversation_id != data.conversation_id
            ):
                raise ResourceConflictError(
                    "Source message must belong to the conversation"
                )
        values["is_active"] = True
        values["created_by_id"] = actor.id
        return values

    async def _validate_search_scope(
        self,
        conversation_id: UUID | None,
        agent_id: UUID | None,
        actor: User,
    ) -> None:
        if conversation_id is not None:
            conversation = await self.conversations.get(conversation_id)
            if conversation is None:
                raise ResourceNotFoundError("Conversation not found")
            if not can_view_conversation(actor, conversation):
                raise PermissionDeniedError
        if agent_id is not None and await self.agents.get(agent_id) is None:
            raise ResourceNotFoundError("Agent not found")

    async def _owned_locked(
        self,
        memory_id: UUID,
        actor: User,
    ) -> AgentMemory:
        memory = await self.memories.get_for_update(memory_id)
        if memory is None:
            raise ResourceNotFoundError("Memory not found")
        if not can_manage_memory(actor, memory):
            raise PermissionDeniedError
        return memory

    async def _commit(
        self,
        entity_id: UUID,
        actor: User,
        action: AuditAction,
    ) -> None:
        _audit(
            self.session,
            actor=actor,
            action=action,
            entity_id=entity_id,
        )
        await self.session.commit()
