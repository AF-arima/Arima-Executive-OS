from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.orchestration.context import OrchestrationExecutionContext
from app.orchestration.factory import OrchestrationFactory
from app.orchestration.schemas import OrchestrationRequest
from app.schemas.agent import ConversationCreateRequest, RunCreateRequest
from app.services.agent import AgentService, ConversationService, RunService
from app.services.permissions import user_roles
from app.voice.gateway import VoiceGateway
from app.voice.schemas import VoiceSession
from app.voice.session import VoiceSessionStore

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "administrator": frozenset({"*"}),
    "executive": frozenset({"*"}),
    "manager": frozenset({"read", "write", "audit"}),
    "analyst": frozenset({"read", "write"}),
    "viewer": frozenset({"read"}),
}

class VoiceOrchestrationContextFactory:
    def __init__(self, database: AsyncSession) -> None:
        self.database = database

    async def __call__(
        self,
        voice_session: VoiceSession,
        actor: User,
        transcript: str,
    ) -> OrchestrationExecutionContext:
        agents = AgentService(self.database)
        conversations = ConversationService(self.database)
        if voice_session.conversation_id is None:
            agent = await agents.get_default()
            conversation = await conversations.create(
                ConversationCreateRequest(
                    agent_id=agent.id,
                    title="Arima voice session",
                    owner_id=actor.id,
                    metadata={
                        "channel": "voice",
                        "voice_session_id": str(voice_session.session_id),
                    },
                ),
                actor,
            )
        else:
            conversation = await conversations.get(
                voice_session.conversation_id, actor
            )
            agent = await agents.get(conversation.agent_id)
        run = await RunService(self.database).create(
            RunCreateRequest(
                conversation_id=conversation.id,
                context_snapshot={
                    "channel": "voice",
                    "locale": voice_session.locale,
                    "timezone": voice_session.timezone,
                },
                metadata={
                    "voice_session_id": str(voice_session.session_id)
                },
            ),
            actor,
        )
        permissions: set[str] = set()
        for role in user_roles(actor):
            permissions.update(ROLE_PERMISSIONS.get(role, ()))
        return OrchestrationExecutionContext(
            user=actor,
            agent=agent,
            conversation=conversation,
            run=run,
            request=OrchestrationRequest(
                content=transcript,
                stream=True,
                metadata={
                    "channel": "browser_voice",
                    "voice_session_id": str(voice_session.session_id),
                },
            ),
            permissions=frozenset(permissions),
            correlation_id=voice_session.correlation_id,
            timezone=voice_session.timezone,
            locale=voice_session.locale,
        )


class VoiceGatewayFactory:
    def __init__(
        self,
        database: AsyncSession,
        *,
        sessions: VoiceSessionStore | None = None,
        enabled: bool = True,
    ) -> None:
        self.database = database
        self.sessions = sessions or VoiceSessionStore(database)
        self.enabled = enabled

    def create(self) -> VoiceGateway:
        return VoiceGateway(
            sessions=self.sessions,
            orchestration=OrchestrationFactory(self.database).create(),
            context_factory=VoiceOrchestrationContextFactory(self.database),
            enabled=self.enabled,
        )
