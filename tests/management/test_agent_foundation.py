import asyncio
from datetime import datetime, timezone

from pydantic import ValidationError
import pytest

from app.database.models import (
    AgentConversation,
    AgentMemoryScope,
    AgentStatus,
    ConversationPriority,
    ConversationStatus,
    Role,
    User,
    UserRole,
)
from app.core.config import Settings
from app.database.repositories.agent import (
    AgentConversationRepository,
    AgentDefinitionRepository,
    AgentMemoryRepository,
)
from app.schemas.agent import (
    AgentConversationCreate,
    AgentDefinitionCreate,
    AgentDefinitionFilter,
    AgentMemoryCreate,
    AgentMemoryType,
    AgentRunCreate,
)
from app.services.agent_bootstrap import (
    DEFAULT_AGENT_SLUG,
    FOUNDATION_TOOLS,
    bootstrap_agent_platform,
    bootstrap_configured_agent_platform,
)
from tests.database.helpers import sqlite_session

UTC = timezone.utc


def test_agent_schema_normalization_and_strict_validation() -> None:
    definition = AgentDefinitionCreate(
        slug="  Executive-Assistant  ",
        name="Executive Assistant",
        system_instructions="Operate safely.",
        status=AgentStatus.ACTIVE,
        created_by_id="00000000-0000-0000-0000-000000000001",
    )
    assert definition.slug == "executive-assistant"

    memory = AgentMemoryCreate(
        owner_id="00000000-0000-0000-0000-000000000001",
        memory_type=AgentMemoryType.PREFERENCE,
        scope=AgentMemoryScope.USER,
        key="  Reporting.Currency_GBP  ",
        value="GBP",
    )
    assert memory.key == "reporting.currency_gbp"

    with pytest.raises(ValidationError):
        AgentDefinitionCreate(
            slug="invalid slug",
            name="Invalid",
            system_instructions="Invalid",
            created_by_id="00000000-0000-0000-0000-000000000001",
        )
    with pytest.raises(ValidationError):
        AgentMemoryCreate(
            memory_type=AgentMemoryType.FACT,
            scope=AgentMemoryScope.ORGANISATION,
            key="invalid/key",
            value="Invalid",
        )
    with pytest.raises(ValidationError):
        AgentDefinitionFilter(unexpected=True)
    with pytest.raises(ValidationError):
        AgentRunCreate(
            conversation_id="00000000-0000-0000-0000-000000000001",
            agent_id="00000000-0000-0000-0000-000000000002",
            triggered_by_id="00000000-0000-0000-0000-000000000003",
            prompt_tokens=-1,
        )
    with pytest.raises(ValidationError):
        AgentRunCreate(
            conversation_id="00000000-0000-0000-0000-000000000001",
            agent_id="00000000-0000-0000-0000-000000000002",
            triggered_by_id="00000000-0000-0000-0000-000000000003",
            started_at=datetime.now(),
        )
    with pytest.raises(ValidationError):
        AgentConversationCreate(
            agent_id="00000000-0000-0000-0000-000000000001",
            owner_id="00000000-0000-0000-0000-000000000002",
            title="Invalid JSON",
            metadata={"not_json": {1, 2}},
        )


def test_agent_bootstrap_is_idempotent_and_preserves_manual_tool_disable() -> None:
    async def exercise() -> None:
        async with sqlite_session() as session:
            creator = User(
                email="bootstrap@example.com",
                hashed_password="not-used",
                first_name="Bootstrap",
                last_name="Owner",
            )
            session.add(creator)
            await session.commit()

            first = await bootstrap_agent_platform(
                session,
                created_by_id=creator.id,
            )
            assert first.agent.slug == DEFAULT_AGENT_SLUG
            assert first.agent.status is AgentStatus.ACTIVE
            assert first.agent.is_default is True
            assert len(first.tools) == len(FOUNDATION_TOOLS) == 9
            first.tools[0].is_enabled = False
            await session.commit()

            second = await bootstrap_agent_platform(
                session,
                created_by_id=creator.id,
            )
            agents = await AgentDefinitionRepository(session).list_scoped(
                include_archived=True
            )
            assert agents.total == 1
            assert second.agent.id == first.agent.id
            assert {tool.id for tool in second.tools} == {
                tool.id for tool in first.tools
            }
            assert len(second.tools) == 9
            disabled = next(
                tool
                for tool in second.tools
                if tool.slug == first.tools[0].slug
            )
            assert disabled.is_enabled is False

            memories = AgentMemoryRepository(session)
            memory_values = AgentMemoryCreate(
                owner_id=creator.id,
                memory_type=AgentMemoryType.PREFERENCE,
                scope=AgentMemoryScope.USER,
                key="reporting.currency",
                value="GBP",
            ).model_dump()
            await memories.create(memory_values)
            with pytest.raises(ValueError, match="already exists"):
                await memories.create(memory_values)

    asyncio.run(exercise())


def test_configured_bootstrap_requires_allowlisted_verified_admin() -> None:
    async def exercise() -> None:
        async with sqlite_session() as session:
            administrator = Role(name="administrator")
            manager = Role(name="manager")
            founder = User(
                email="founder@example.com",
                hashed_password="not-used",
                first_name="Founder",
                last_name="Admin",
                is_verified=True,
            )
            other_admin = User(
                email="other-admin@example.com",
                hashed_password="not-used",
                first_name="Other",
                last_name="Admin",
                is_verified=True,
            )
            allowlisted_manager = User(
                email="manager@example.com",
                hashed_password="not-used",
                first_name="Allowed",
                last_name="Manager",
                is_verified=True,
            )
            session.add_all(
                [
                    administrator,
                    manager,
                    founder,
                    other_admin,
                    allowlisted_manager,
                ]
            )
            await session.flush()
            session.add_all(
                [
                    UserRole(
                        user_id=founder.id,
                        role_id=administrator.id,
                    ),
                    UserRole(
                        user_id=other_admin.id,
                        role_id=administrator.id,
                    ),
                    UserRole(
                        user_id=allowlisted_manager.id,
                        role_id=manager.id,
                    ),
                ]
            )
            await session.commit()

            result = await bootstrap_configured_agent_platform(
                session,
                settings=Settings(
                    founder_control_emails=[
                        "founder@example.com",
                        "manager@example.com",
                    ]
                ),
            )

            assert result.agent.created_by_id == founder.id
            assert result.agent.is_default is True

    asyncio.run(exercise())


def test_agent_repository_pagination_filters_and_archive_defaults() -> None:
    async def exercise() -> None:
        async with sqlite_session() as session:
            owner = User(
                email="pagination@example.com",
                hashed_password="not-used",
                first_name="Page",
                last_name="Owner",
            )
            other = User(
                email="other-pagination@example.com",
                hashed_password="not-used",
                first_name="Other",
                last_name="Owner",
            )
            session.add_all([owner, other])
            await session.flush()
            bootstrap = await bootstrap_agent_platform(
                session,
                created_by_id=owner.id,
            )
            conversations = AgentConversationRepository(session)
            pinned = await conversations.create(
                AgentConversationCreate(
                    agent_id=bootstrap.agent.id,
                    owner_id=owner.id,
                    title="Pinned",
                    status=ConversationStatus.ACTIVE,
                    priority=ConversationPriority.URGENT,
                    pinned=True,
                    metadata={"created_through": "repository"},
                ).model_dump()
            )
            rows = [
                pinned,
                AgentConversation(
                    agent_id=bootstrap.agent.id,
                    owner_id=owner.id,
                    title="Normal",
                    status=ConversationStatus.ACTIVE,
                    priority=ConversationPriority.NORMAL,
                    pinned=False,
                ),
                AgentConversation(
                    agent_id=bootstrap.agent.id,
                    owner_id=other.id,
                    title="Other",
                    status=ConversationStatus.ACTIVE,
                    priority=ConversationPriority.NORMAL,
                ),
            ]
            session.add_all(rows)
            await session.commit()

            page_one = await conversations.list_scoped(
                owner_id=owner.id,
                status=ConversationStatus.ACTIVE,
                limit=1,
                offset=0,
            )
            page_two = await conversations.list_scoped(
                owner_id=owner.id,
                status=ConversationStatus.ACTIVE,
                limit=1,
                offset=1,
            )
            assert page_one.total == 2
            assert page_two.total == 2
            assert page_one.items[0].id != page_two.items[0].id

            archived_at = datetime.now(UTC)
            await conversations.archive(
                rows[0],
                archived_at=archived_at,
            )
            await session.commit()
            visible = await conversations.list_scoped(owner_id=owner.id)
            all_rows = await conversations.list_scoped(
                owner_id=owner.id,
                include_archived=True,
            )
            assert visible.total == 1
            assert all_rows.total == 2

            await AgentDefinitionRepository(session).archive(
                bootstrap.agent,
                archived_at=archived_at,
            )
            await session.commit()
            agents = AgentDefinitionRepository(session)
            assert await agents.get_by_slug(DEFAULT_AGENT_SLUG) is None
            assert (
                await agents.get_by_slug(
                    DEFAULT_AGENT_SLUG,
                    include_archived=True,
                )
                is not None
            )
            assert await agents.get_active_default() is None

    asyncio.run(exercise())
