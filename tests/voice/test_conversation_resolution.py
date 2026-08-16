import pytest

from app.voice.factory import VoiceGatewayFactory
from app.voice.schemas import VoiceSessionCreate
from app.voice.session import VoiceSessionStore
from tests.database.helpers import sqlite_session
from tests.intelligence.helpers import make_intelligence_context


@pytest.mark.asyncio
async def test_voice_uses_existing_workspace_bound_conversation() -> None:
    async with sqlite_session() as database:
        seed = await make_intelligence_context(database)

        session, _ = await VoiceGatewayFactory(
            database,
            sessions=VoiceSessionStore(database),
        ).create().create_session(VoiceSessionCreate(), seed.user)

        assert session.conversation_id == seed.conversation.id


@pytest.mark.asyncio
async def test_voice_skips_legacy_conversation_and_creates_workspace_bound_one() -> None:
    async with sqlite_session() as database:
        seed = await make_intelligence_context(database)
        seed.conversation.metadata_ = {}
        seed.agent.is_default = True
        await database.commit()

        session, _ = await VoiceGatewayFactory(
            database,
            sessions=VoiceSessionStore(database),
        ).create().create_session(VoiceSessionCreate(), seed.user)

        assert session.conversation_id != seed.conversation.id
        legacy = await database.get(type(seed.conversation), seed.conversation.id)
        fresh = await database.get(type(seed.conversation), session.conversation_id)
        assert legacy is not None
        assert legacy.metadata_ == {}
        assert fresh is not None
        assert fresh.owner_id == seed.user.id
        assert fresh.metadata_["workspace_id"] == str(seed.workspace.id)
