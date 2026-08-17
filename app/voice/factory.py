from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import AgentRunStatus, MessageRole, User
from app.intelligence.access import (
    AgentGrantService,
    IntelligenceAccessError,
    RunBindingService,
    require_workspace_membership,
)
from app.intelligence.retrieval import TenantSafeRetrievalService
from app.intelligence.schemas import RetrievalQuery, RetrievedKnowledge
from app.orchestration.context import OrchestrationExecutionContext
from app.orchestration.factory import OrchestrationFactory
from app.orchestration.schemas import OrchestrationRequest
from app.schemas.agent import (
    MessageCreateRequest,
    RunCreateRequest,
    RunTransitionRequest,
)
from app.services.agent import (
    AgentService,
    ConversationService,
    MessageService,
    RunService,
)
from app.services.exceptions import (
    PermissionDeniedError,
    ResourceNotFoundError,
)
from app.services.permissions import user_roles
from app.voice.gateway import VoiceGateway
from app.voice.conversation import VoiceConversationResolver
from app.voice.exceptions import VoicePermissionDenied
from app.voice.orchestration import DurableVoiceOrchestration
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
            raise VoicePermissionDenied(
                "An authorized existing conversation is required for Voice AI"
            )
        try:
            conversation = await conversations.get(
                voice_session.conversation_id, actor
            )
            agent = await agents.get(conversation.agent_id)
            workspace_id = UUID(str(conversation.metadata_["workspace_id"]))
            await require_workspace_membership(
                self.database, actor, workspace_id
            )
            await AgentGrantService(self.database).require(
                workspace_id=workspace_id,
                agent_id=agent.id,
            )
        except (
            IntelligenceAccessError,
            KeyError,
            PermissionDeniedError,
            ResourceNotFoundError,
            ValueError,
        ) as error:
            raise VoicePermissionDenied(
                "Voice AI authorization denied"
            ) from error
        input_message = await MessageService(
            self.database
        ).create_user_message(
            MessageCreateRequest(
                conversation_id=conversation.id,
                role=MessageRole.USER,
                content=transcript,
                metadata={
                    "channel": "voice",
                    "voice_session_id": str(voice_session.session_id),
                },
            ),
            actor,
        )
        runs = RunService(self.database)
        run = await runs.create(
            RunCreateRequest(
                conversation_id=conversation.id,
                input_message_id=input_message.id,
                context_snapshot={
                    "channel": "voice",
                    "workspace_id": str(workspace_id),
                    "locale": voice_session.locale,
                    "timezone": voice_session.timezone,
                },
                metadata={
                    "voice_session_id": str(voice_session.session_id)
                },
            ),
            actor,
        )
        binding = await RunBindingService(self.database).bind(
            workspace_id=workspace_id,
            run=run,
            actor=actor,
            channel="voice",
        )
        run = await runs.start(run.id, actor)
        try:
            evidence = await TenantSafeRetrievalService(
                self.database
            ).retrieve(
                workspace_id=workspace_id,
                run_id=run.id,
                actor=actor,
                query=RetrievalQuery(text=transcript),
            )
        except Exception as error:
            await runs.fail(
                run.id,
                RunTransitionRequest(
                    status=AgentRunStatus.FAILED,
                    failure_code="voice_retrieval_failed",
                    failure_message=(
                        f"Voice retrieval failed ({type(error).__name__})"
                    ),
                ),
                actor,
            )
            raise
        content = self._context_text(transcript, evidence)
        permissions: set[str] = set()
        for role in user_roles(actor):
            permissions.update(ROLE_PERMISSIONS.get(role, ()))
        return OrchestrationExecutionContext(
            user=actor,
            agent=agent,
            conversation=conversation,
            run=run,
            request=OrchestrationRequest(
                content=content,
                stream=True,
                metadata={
                    "channel": "browser_voice",
                    "workspace_id": str(workspace_id),
                    "voice_session_id": str(voice_session.session_id),
                    "evidence_ids": [
                        str(item.evidence_id) for item in evidence
                    ],
                },
            ),
            permissions=frozenset(permissions),
            correlation_id=binding.correlation_id,
            timezone=voice_session.timezone,
            locale=voice_session.locale,
        )

    @staticmethod
    def _context_text(
        transcript: str,
        evidence: tuple[RetrievedKnowledge, ...],
    ) -> str:
        if not evidence:
            return transcript
        rendered = "\n\n".join(
            f"[evidence:{item.evidence_id}] {item.content}"
            for item in evidence
        )
        return (
            f"{transcript}\n\nApproved workspace context follows. Cite "
            f"evidence IDs for factual claims:\n{rendered}"
        )


class VoiceGatewayFactory:
    def __init__(
        self,
        database: AsyncSession,
        *,
        sessions: VoiceSessionStore | None = None,
        enabled: bool = True,
        session_timeout_seconds: int = 1_800,
    ) -> None:
        self.database = database
        self.sessions = sessions or VoiceSessionStore(database)
        self.enabled = enabled
        self.session_timeout_seconds = session_timeout_seconds

    def create(self) -> VoiceGateway:
        orchestration = OrchestrationFactory(self.database).create()
        return VoiceGateway(
            sessions=self.sessions,
            orchestration=DurableVoiceOrchestration(
                self.database, orchestration
            ),
            context_factory=VoiceOrchestrationContextFactory(self.database),
            conversation_resolver=VoiceConversationResolver(self.database),
            enabled=self.enabled,
            stale_session_timeout=timedelta(
                seconds=self.session_timeout_seconds
            ),
        )
