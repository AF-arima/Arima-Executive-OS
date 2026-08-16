import pytest

from app.intelligence.access import AgentGrantService
from app.voice.exceptions import VoicePermissionDenied
from app.voice.factory import VoiceGatewayFactory, VoiceOrchestrationContextFactory
from app.voice.schemas import VoiceSessionCreate
from app.voice.session import VoiceSessionStore
from tests.database.helpers import sqlite_session
from tests.intelligence.helpers import make_intelligence_context


@pytest.mark.asyncio
async def test_resolver_does_not_create_conversation_without_default_agent_grant() -> None:
    """The resolver may select a correctly workspace-bound conversation
    whose agent has no active workspace grant; this must remain fail-closed.
    """
    async with sqlite_session() as database:
        seed = await make_intelligence_context(database)
        seed.agent.is_default = True
        await database.commit()

        with pytest.raises(VoicePermissionDenied, match="Voice AI authorization denied"):
            await VoiceGatewayFactory(
                database,
                sessions=VoiceSessionStore(database),
            ).create().create_session(VoiceSessionCreate(), seed.user)


@pytest.mark.asyncio
async def test_resolver_accepts_conversation_after_workspace_grant() -> None:
    async with sqlite_session() as database:
        seed = await make_intelligence_context(database)
        await AgentGrantService(database).grant(
            workspace_id=seed.workspace.id,
            agent_id=seed.agent.id,
            actor=seed.user,
        )

        voice_session, _ = await VoiceGatewayFactory(
            database,
            sessions=VoiceSessionStore(database),
        ).create().create_session(VoiceSessionCreate(), seed.user)

        assert voice_session.conversation_id == seed.conversation.id
        context = await VoiceOrchestrationContextFactory(database)(
            voice_session,
                seed.user,
                "Analyse this decision",
        )
        assert context.conversation.id == seed.conversation.id
