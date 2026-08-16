from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import ConversationStatus, User
from app.database.repositories.agent import AgentDefinitionRepository
from app.database.repositories.workspace import WorkspaceRepository
from app.schemas.agent import ConversationCreateRequest
from app.services.agent import ConversationService
from app.services.exceptions import (
    PermissionDeniedError,
    ResourceConflictError,
    ResourceNotFoundError,
)
from app.voice.exceptions import VoicePermissionDenied


class VoiceConversationResolver:
    """Choose a canonical workspace-bound conversation for a Voice session."""

    def __init__(self, database: AsyncSession) -> None:
        self.database = database

    async def resolve(self, actor: User) -> UUID:
        workspace = await WorkspaceRepository(self.database).get_by_owner(actor.id)
        if workspace is None:
            raise VoicePermissionDenied("Voice AI authorization denied")
        expected_workspace_id = str(workspace.id)
        conversations = await ConversationService(
            self.database
        ).list_user_conversations(
            actor,
            owner_id=actor.id,
            agent_id=None,
            status=ConversationStatus.ACTIVE,
            pinned=None,
            include_archived=False,
            limit=50,
            offset=0,
        )
        for conversation in conversations.items:
            if (
                conversation.owner_id == actor.id
                and conversation.metadata_.get("workspace_id")
                == expected_workspace_id
            ):
                return conversation.id
        return await self._create_workspace_bound_conversation(actor)

    async def _create_workspace_bound_conversation(self, actor: User) -> UUID:
        agent = await AgentDefinitionRepository(self.database).get_active_default()
        if agent is None:
            raise VoicePermissionDenied("Voice AI authorization denied")
        try:
            conversation = await ConversationService(self.database).create(
                ConversationCreateRequest(
                    agent_id=agent.id,
                    title="Voice conversation",
                ),
                actor,
            )
        except (
            PermissionDeniedError,
            ResourceConflictError,
            ResourceNotFoundError,
        ) as error:
            raise VoicePermissionDenied("Voice AI authorization denied") from error
        return conversation.id
