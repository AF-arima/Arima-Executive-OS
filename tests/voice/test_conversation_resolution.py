import pytest

from app.database.models import User, WorkspaceMembership
from app.intelligence.access import AgentGrantService
from app.voice.factory import VoiceGatewayFactory
from app.voice.factory import VoiceOrchestrationContextFactory
from app.voice.schemas import VoiceSessionCreate
from app.voice.session import VoiceSessionStore
from tests.database.helpers import sqlite_session
from tests.intelligence.helpers import make_intelligence_context


@pytest.mark.asyncio
async def test_voice_uses_existing_workspace_bound_conversation() -> None:
    async with sqlite_session() as database:
        seed = await make_intelligence_context(database)
        await AgentGrantService(database).grant(
            workspace_id=seed.workspace.id,
            agent_id=seed.agent.id,
            actor=seed.user,
        )

        session, _ = await VoiceGatewayFactory(
            database,
            sessions=VoiceSessionStore(database),
        ).create().create_session(VoiceSessionCreate(), seed.user)

        assert session.conversation_id == seed.conversation.id


@pytest.mark.asyncio
async def test_voice_resolves_canonical_workspace_for_authorized_member() -> None:
    async with sqlite_session() as database:
        seed = await make_intelligence_context(database)
        seed.agent.is_default = True
        await database.commit()
        member = User(
            email="phase4-member@example.com",
            hashed_password="hash",
            first_name="Workspace",
            last_name="Member",
            is_active=True,
            is_verified=True,
        )
        member.roles = list(seed.user.roles)
        database.add(member)
        await database.flush()
        database.add(
            WorkspaceMembership(
                workspace_id=seed.workspace.id,
                user_id=member.id,
                role="member",
            )
        )
        await AgentGrantService(database).grant(
            workspace_id=seed.workspace.id,
            agent_id=seed.agent.id,
            actor=seed.user,
        )

        session, _ = await VoiceGatewayFactory(
            database,
            sessions=VoiceSessionStore(database),
        ).create().create_session(VoiceSessionCreate(), member)

        assert session.user_id == member.id
        assert session.conversation_id is not None
        conversation = await database.get(type(seed.conversation), session.conversation_id)
        assert conversation is not None
        assert conversation.owner_id == member.id
        assert conversation.metadata_["workspace_id"] == str(seed.workspace.id)


@pytest.mark.asyncio
async def test_voice_skips_legacy_conversation_and_creates_workspace_bound_one() -> None:
    async with sqlite_session() as database:
        seed = await make_intelligence_context(database)
        seed.conversation.metadata_ = {}
        seed.agent.is_default = True
        await database.commit()
        await AgentGrantService(database).grant(
            workspace_id=seed.workspace.id,
            agent_id=seed.agent.id,
            actor=seed.user,
        )

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


@pytest.mark.asyncio
async def test_fresh_workspace_bound_conversation_reaches_voice_authorization() -> None:
    async with sqlite_session() as database:
        seed = await make_intelligence_context(database)
        seed.conversation.metadata_ = {}
        seed.agent.is_default = True
        await database.commit()
        await AgentGrantService(database).grant(
            workspace_id=seed.workspace.id,
            agent_id=seed.agent.id,
            actor=seed.user,
        )

        session, _ = await VoiceGatewayFactory(
            database,
            sessions=VoiceSessionStore(database),
        ).create().create_session(VoiceSessionCreate(), seed.user)
        assert session.conversation_id is not None
        fresh = await database.get(type(seed.conversation), session.conversation_id)
        assert fresh is not None
        context = await VoiceOrchestrationContextFactory(database)(
            session,
            seed.user,
            "What is the current risk posture?",
        )

        assert context.conversation.id == fresh.id
        assert context.request.metadata["workspace_id"] == str(seed.workspace.id)
