from collections.abc import Mapping
from datetime import datetime
from typing import Any, Generic, TypeVar
from uuid import UUID

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database.models.agent import (
    AgentApproval,
    AgentApprovalStatus,
    AgentAttachment,
    AgentAttachmentStatus,
    AgentContextSnapshot,
    AgentConversation,
    AgentDefinition,
    AgentMemory,
    AgentMemoryScope,
    AgentMemoryType,
    AgentMessage,
    AgentRiskLevel,
    AgentRun,
    AgentRunStatus,
    AgentStatus,
    AgentToolDefinition,
    AgentToolExecution,
    ConversationPriority,
    ConversationStatus,
    MessageRole,
    ToolExecutionMode,
    ToolExecutionStatus,
)
from app.database.models.base import Base
from app.database.repositories.base import AsyncRepository
from app.database.repositories.pagination import Page, paginate

AgentModel = TypeVar("AgentModel", bound=Base)


class AgentRepositoryBase(AsyncRepository[AgentModel], Generic[AgentModel]):
    async def get_for_update(self, identity: object) -> AgentModel | None:
        return await self.session.scalar(
            select(self.model)
            .where(self.model.__table__.c.id == identity)
            .with_for_update()
        )

    async def create(self, values: Mapping[str, Any]) -> AgentModel:
        instance = self.model(**self._model_values(values))
        return await self.add(instance)

    async def update(
        self,
        instance: AgentModel,
        values: Mapping[str, Any],
    ) -> AgentModel:
        for field_name, value in self._model_values(values).items():
            setattr(instance, field_name, value)
        await self.session.flush()
        await self.session.refresh(instance)
        return instance

    def _model_values(
        self,
        values: Mapping[str, Any],
    ) -> dict[str, Any]:
        data = dict(values)
        if "metadata" in data and hasattr(self.model, "metadata_"):
            data["metadata_"] = data.pop("metadata")
        return data


class AgentDefinitionRepository(AgentRepositoryBase[AgentDefinition]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(AgentDefinition, session)

    async def get_by_slug(
        self,
        slug: str,
        *,
        include_archived: bool = False,
    ) -> AgentDefinition | None:
        statement = select(AgentDefinition).where(
            AgentDefinition.slug == slug
        )
        if not include_archived:
            statement = statement.where(
                AgentDefinition.archived_at.is_(None),
                AgentDefinition.status != AgentStatus.ARCHIVED,
            )
        return await self.session.scalar(statement)

    async def get_active_default(self) -> AgentDefinition | None:
        return await self.session.scalar(
            select(AgentDefinition).where(
                AgentDefinition.status == AgentStatus.ACTIVE,
                AgentDefinition.is_default.is_(True),
                AgentDefinition.archived_at.is_(None),
            )
        )

    async def set_active_default(
        self,
        agent_id: UUID,
    ) -> AgentDefinition | None:
        target = await self.session.scalar(
            select(AgentDefinition)
            .where(AgentDefinition.id == agent_id)
            .with_for_update()
        )
        if target is None:
            return None
        defaults = await self.session.scalars(
            select(AgentDefinition)
            .where(
                AgentDefinition.is_default.is_(True),
                AgentDefinition.id != agent_id,
            )
            .order_by(AgentDefinition.id)
            .with_for_update()
        )
        for existing in defaults:
            existing.is_default = False
        await self.session.flush()
        target.status = AgentStatus.ACTIVE
        target.archived_at = None
        target.is_default = True
        await self.session.flush()
        return target

    async def archive(
        self,
        instance: AgentDefinition,
        *,
        archived_at: datetime,
    ) -> AgentDefinition:
        instance.status = AgentStatus.ARCHIVED
        instance.is_default = False
        instance.archived_at = archived_at
        await self.session.flush()
        return instance

    async def list_scoped(
        self,
        *,
        status: AgentStatus | None = None,
        is_default: bool | None = None,
        include_archived: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> Page[AgentDefinition]:
        statement = select(AgentDefinition)
        if not include_archived:
            statement = statement.where(
                AgentDefinition.archived_at.is_(None),
                AgentDefinition.status != AgentStatus.ARCHIVED,
            )
        if status is not None:
            statement = statement.where(AgentDefinition.status == status)
        if is_default is not None:
            statement = statement.where(
                AgentDefinition.is_default == is_default
            )
        return await paginate(
            self.session,
            statement.order_by(
                AgentDefinition.created_at.desc(),
                AgentDefinition.id.desc(),
            ),
            limit=limit,
            offset=offset,
        )


class AgentConversationRepository(
    AgentRepositoryBase[AgentConversation]
):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(AgentConversation, session)

    async def archive(
        self,
        instance: AgentConversation,
        *,
        archived_at: datetime,
    ) -> AgentConversation:
        instance.status = ConversationStatus.ARCHIVED
        instance.archived_at = archived_at
        await self.session.flush()
        return instance

    async def list_scoped(
        self,
        *,
        owner_id: UUID | None = None,
        agent_id: UUID | None = None,
        status: ConversationStatus | None = None,
        priority: ConversationPriority | None = None,
        pinned: bool | None = None,
        include_archived: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> Page[AgentConversation]:
        statement = select(AgentConversation)
        if not include_archived:
            statement = statement.where(
                AgentConversation.archived_at.is_(None),
                AgentConversation.status != ConversationStatus.ARCHIVED,
            )
        if owner_id is not None:
            statement = statement.where(
                AgentConversation.owner_id == owner_id
            )
        if agent_id is not None:
            statement = statement.where(
                AgentConversation.agent_id == agent_id
            )
        if status is not None:
            statement = statement.where(
                AgentConversation.status == status
            )
        if priority is not None:
            statement = statement.where(
                AgentConversation.priority == priority
            )
        if pinned is not None:
            statement = statement.where(AgentConversation.pinned == pinned)
        return await paginate(
            self.session,
            statement.order_by(
                AgentConversation.updated_at.desc(),
                AgentConversation.id.desc(),
            ),
            limit=limit,
            offset=offset,
        )


class AgentMessageRepository(AgentRepositoryBase[AgentMessage]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(AgentMessage, session)

    async def next_sequence(self, conversation_id: UUID) -> int:
        conversation = await self.session.scalar(
            select(AgentConversation.id)
            .where(AgentConversation.id == conversation_id)
            .with_for_update()
        )
        if conversation is None:
            raise LookupError("Conversation not found")
        current = await self.session.scalar(
            select(func.max(AgentMessage.sequence_number)).where(
                AgentMessage.conversation_id == conversation_id
            )
        )
        return int(current or 0) + 1

    async def create_sequenced(
        self,
        conversation: AgentConversation,
        values: Mapping[str, Any],
        *,
        created_at: datetime,
    ) -> AgentMessage:
        sequence_number = await self.next_sequence(conversation.id)
        message = await self.create(
            {
                **values,
                "conversation_id": conversation.id,
                "sequence_number": sequence_number,
                "created_at": created_at,
            }
        )
        await AgentConversationRepository(self.session).update(
            conversation,
            {"last_message_at": created_at},
        )
        return message

    async def list_for_conversation(
        self,
        conversation_id: UUID,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> Page[AgentMessage]:
        return await paginate(
            self.session,
            select(AgentMessage)
            .where(AgentMessage.conversation_id == conversation_id)
            .order_by(
                AgentMessage.sequence_number,
                AgentMessage.id,
            ),
            limit=limit,
            offset=offset,
        )

    async def list_scoped(
        self,
        *,
        conversation_id: UUID | None = None,
        run_id: UUID | None = None,
        role: MessageRole | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Page[AgentMessage]:
        statement = select(AgentMessage)
        if conversation_id is not None:
            statement = statement.where(
                AgentMessage.conversation_id == conversation_id
            )
        if run_id is not None:
            statement = statement.where(AgentMessage.run_id == run_id)
        if role is not None:
            statement = statement.where(AgentMessage.role == role)
        return await paginate(
            self.session,
            statement.order_by(
                AgentMessage.created_at.desc(),
                AgentMessage.id.desc(),
            ),
            limit=limit,
            offset=offset,
        )


class AgentRunRepository(AgentRepositoryBase[AgentRun]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(AgentRun, session)

    async def get_with_related(self, run_id: UUID) -> AgentRun | None:
        return await self.session.scalar(
            select(AgentRun)
            .where(AgentRun.id == run_id)
            .options(
                selectinload(AgentRun.agent),
                selectinload(AgentRun.conversation),
                selectinload(AgentRun.tool_executions).selectinload(
                    AgentToolExecution.tool
                ),
                selectinload(AgentRun.approvals),
                selectinload(AgentRun.snapshot),
            )
        )

    async def list_scoped(
        self,
        *,
        conversation_id: UUID | None = None,
        agent_id: UUID | None = None,
        triggered_by_id: UUID | None = None,
        status: AgentRunStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Page[AgentRun]:
        statement: Select[tuple[AgentRun]] = select(AgentRun).options(
            selectinload(AgentRun.agent),
            selectinload(AgentRun.conversation),
            selectinload(AgentRun.tool_executions).selectinload(
                AgentToolExecution.tool
            ),
            selectinload(AgentRun.approvals),
            selectinload(AgentRun.snapshot),
        )
        if conversation_id is not None:
            statement = statement.where(
                AgentRun.conversation_id == conversation_id
            )
        if agent_id is not None:
            statement = statement.where(AgentRun.agent_id == agent_id)
        if triggered_by_id is not None:
            statement = statement.where(
                AgentRun.triggered_by_id == triggered_by_id
            )
        if status is not None:
            statement = statement.where(AgentRun.status == status)
        return await paginate(
            self.session,
            statement.order_by(
                AgentRun.created_at.desc(),
                AgentRun.id.desc(),
            ),
            limit=limit,
            offset=offset,
        )


class AgentToolDefinitionRepository(
    AgentRepositoryBase[AgentToolDefinition]
):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(AgentToolDefinition, session)

    async def get_by_slug(self, slug: str) -> AgentToolDefinition | None:
        return await self.session.scalar(
            select(AgentToolDefinition).where(
                AgentToolDefinition.slug == slug
            )
        )

    async def list_enabled(self) -> list[AgentToolDefinition]:
        rows = await self.session.scalars(
            select(AgentToolDefinition)
            .where(AgentToolDefinition.is_enabled.is_(True))
            .order_by(AgentToolDefinition.slug)
        )
        return list(rows.all())

    async def soft_disable(
        self,
        instance: AgentToolDefinition,
    ) -> AgentToolDefinition:
        instance.is_enabled = False
        await self.session.flush()
        return instance

    async def list_scoped(
        self,
        *,
        slug: str | None = None,
        category: str | None = None,
        risk_level: AgentRiskLevel | None = None,
        execution_mode: ToolExecutionMode | None = None,
        is_enabled: bool | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Page[AgentToolDefinition]:
        statement = select(AgentToolDefinition)
        if slug is not None:
            statement = statement.where(AgentToolDefinition.slug == slug)
        if category is not None:
            statement = statement.where(
                AgentToolDefinition.category == category
            )
        if risk_level is not None:
            statement = statement.where(
                AgentToolDefinition.risk_level == risk_level
            )
        if execution_mode is not None:
            statement = statement.where(
                AgentToolDefinition.execution_mode == execution_mode
            )
        if is_enabled is not None:
            statement = statement.where(
                AgentToolDefinition.is_enabled == is_enabled
            )
        return await paginate(
            self.session,
            statement.order_by(AgentToolDefinition.slug),
            limit=limit,
            offset=offset,
        )


class AgentToolExecutionRepository(
    AgentRepositoryBase[AgentToolExecution]
):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(AgentToolExecution, session)

    async def list_scoped(
        self,
        *,
        run_id: UUID | None = None,
        tool_id: UUID | None = None,
        status: ToolExecutionStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Page[AgentToolExecution]:
        statement = select(AgentToolExecution).options(
            selectinload(AgentToolExecution.tool)
        )
        if run_id is not None:
            statement = statement.where(
                AgentToolExecution.run_id == run_id
            )
        if tool_id is not None:
            statement = statement.where(
                AgentToolExecution.tool_id == tool_id
            )
        if status is not None:
            statement = statement.where(
                AgentToolExecution.status == status
            )
        return await paginate(
            self.session,
            statement.order_by(
                AgentToolExecution.created_at.desc(),
                AgentToolExecution.id.desc(),
            ),
            limit=limit,
            offset=offset,
        )


class AgentApprovalRepository(AgentRepositoryBase[AgentApproval]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(AgentApproval, session)

    async def list_pending_unexpired(
        self,
        *,
        now: datetime,
        limit: int = 50,
        offset: int = 0,
    ) -> Page[AgentApproval]:
        return await paginate(
            self.session,
            select(AgentApproval)
            .where(
                AgentApproval.status == AgentApprovalStatus.PENDING,
                or_(
                    AgentApproval.expires_at.is_(None),
                    AgentApproval.expires_at > now,
                ),
            )
            .order_by(
                AgentApproval.requested_at,
                AgentApproval.id,
            ),
            limit=limit,
            offset=offset,
        )

    async def list_scoped(
        self,
        *,
        run_id: UUID | None = None,
        status: AgentApprovalStatus | None = None,
        requested_by_id: UUID | None = None,
        decided_by_id: UUID | None = None,
        unexpired_at: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Page[AgentApproval]:
        statement = select(AgentApproval)
        if run_id is not None:
            statement = statement.where(AgentApproval.run_id == run_id)
        if status is not None:
            statement = statement.where(AgentApproval.status == status)
        if requested_by_id is not None:
            statement = statement.where(
                AgentApproval.requested_by_id == requested_by_id
            )
        if decided_by_id is not None:
            statement = statement.where(
                AgentApproval.decided_by_id == decided_by_id
            )
        if unexpired_at is not None:
            statement = statement.where(
                or_(
                    AgentApproval.expires_at.is_(None),
                    AgentApproval.expires_at > unexpired_at,
                )
            )
        return await paginate(
            self.session,
            statement.order_by(
                AgentApproval.requested_at.desc(),
                AgentApproval.id.desc(),
            ),
            limit=limit,
            offset=offset,
        )


class AgentMemoryRepository(AgentRepositoryBase[AgentMemory]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(AgentMemory, session)

    async def create(self, values: Mapping[str, Any]) -> AgentMemory:
        scope = values.get("scope")
        key = values.get("key")
        if not isinstance(scope, AgentMemoryScope) or not isinstance(key, str):
            raise ValueError("Memory scope and key are required")
        existing = await self.get_by_scope_key(
            scope=scope,
            key=key,
            owner_id=values.get("owner_id"),
            agent_id=values.get("agent_id"),
            conversation_id=values.get("conversation_id"),
        )
        if existing is not None:
            raise ValueError("Memory key already exists in this scope")
        return await super().create(values)

    async def get_by_scope_key(
        self,
        *,
        scope: AgentMemoryScope,
        key: str,
        owner_id: object = None,
        agent_id: object = None,
        conversation_id: object = None,
    ) -> AgentMemory | None:
        return await self.session.scalar(
            select(AgentMemory).where(
                AgentMemory.scope == scope,
                AgentMemory.key == key,
                AgentMemory.owner_id == owner_id,
                AgentMemory.agent_id == agent_id,
                AgentMemory.conversation_id == conversation_id,
            )
        )

    async def soft_disable(self, instance: AgentMemory) -> AgentMemory:
        instance.is_active = False
        await self.session.flush()
        return instance

    async def list_active_unexpired(
        self,
        *,
        scope: AgentMemoryScope,
        now: datetime,
        owner_id: UUID | None = None,
        agent_id: UUID | None = None,
        conversation_id: UUID | None = None,
        key: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Page[AgentMemory]:
        return await self.list_scoped(
            owner_id=owner_id,
            agent_id=agent_id,
            conversation_id=conversation_id,
            scope=scope,
            key=key,
            active_unexpired_at=now,
            limit=limit,
            offset=offset,
        )

    async def list_scoped(
        self,
        *,
        owner_id: UUID | None = None,
        agent_id: UUID | None = None,
        conversation_id: UUID | None = None,
        memory_type: AgentMemoryType | None = None,
        scope: AgentMemoryScope | None = None,
        key: str | None = None,
        active_unexpired_at: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Page[AgentMemory]:
        statement = select(AgentMemory)
        if owner_id is not None:
            statement = statement.where(AgentMemory.owner_id == owner_id)
        if agent_id is not None:
            statement = statement.where(AgentMemory.agent_id == agent_id)
        if conversation_id is not None:
            statement = statement.where(
                AgentMemory.conversation_id == conversation_id
            )
        if memory_type is not None:
            statement = statement.where(
                AgentMemory.memory_type == memory_type
            )
        if scope is not None:
            statement = statement.where(AgentMemory.scope == scope)
        if key is not None:
            statement = statement.where(AgentMemory.key == key)
        if active_unexpired_at is not None:
            statement = statement.where(
                AgentMemory.is_active.is_(True),
                or_(
                    AgentMemory.expires_at.is_(None),
                    AgentMemory.expires_at > active_unexpired_at,
                ),
            )
        return await paginate(
            self.session,
            statement.order_by(
                AgentMemory.importance.desc(),
                AgentMemory.updated_at.desc(),
                AgentMemory.id.desc(),
            ),
            limit=limit,
            offset=offset,
        )


class AgentContextSnapshotRepository(
    AgentRepositoryBase[AgentContextSnapshot]
):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(AgentContextSnapshot, session)

    async def get_by_run(
        self,
        run_id: UUID,
    ) -> AgentContextSnapshot | None:
        return await self.session.scalar(
            select(AgentContextSnapshot).where(
                AgentContextSnapshot.run_id == run_id
            )
        )

    async def list_scoped(
        self,
        *,
        run_id: UUID | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Page[AgentContextSnapshot]:
        statement = select(AgentContextSnapshot)
        if run_id is not None:
            statement = statement.where(
                AgentContextSnapshot.run_id == run_id
            )
        return await paginate(
            self.session,
            statement.order_by(
                AgentContextSnapshot.created_at.desc(),
                AgentContextSnapshot.id.desc(),
            ),
            limit=limit,
            offset=offset,
        )


class AgentAttachmentRepository(
    AgentRepositoryBase[AgentAttachment]
):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(AgentAttachment, session)

    async def soft_delete(
        self,
        instance: AgentAttachment,
    ) -> AgentAttachment:
        instance.status = AgentAttachmentStatus.DELETED
        await self.session.flush()
        return instance

    async def list_scoped(
        self,
        *,
        conversation_id: UUID | None = None,
        message_id: UUID | None = None,
        uploaded_by_id: UUID | None = None,
        status: AgentAttachmentStatus | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> Page[AgentAttachment]:
        statement = select(AgentAttachment)
        if conversation_id is not None:
            statement = statement.where(
                AgentAttachment.conversation_id == conversation_id
            )
        if message_id is not None:
            statement = statement.where(
                AgentAttachment.message_id == message_id
            )
        if uploaded_by_id is not None:
            statement = statement.where(
                AgentAttachment.uploaded_by_id == uploaded_by_id
            )
        if status is not None:
            statement = statement.where(AgentAttachment.status == status)
        return await paginate(
            self.session,
            statement.order_by(
                AgentAttachment.created_at.desc(),
                AgentAttachment.id.desc(),
            ),
            limit=limit,
            offset=offset,
        )
