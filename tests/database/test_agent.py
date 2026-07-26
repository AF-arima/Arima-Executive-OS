import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from alembic import command
from alembic.config import Config
import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.database.models import (
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
    MessageContentType,
    MessageRole,
    ToolExecutionMode,
    ToolExecutionStatus,
    User,
)
from app.database.repositories.agent import (
    AgentApprovalRepository,
    AgentDefinitionRepository,
    AgentMemoryRepository,
    AgentMessageRepository,
    AgentRunRepository,
    AgentToolDefinitionRepository,
)
from tests.database.helpers import sqlite_session

UTC = timezone.utc


def _user(email: str = "agent-owner@example.com") -> User:
    return User(
        email=email,
        hashed_password="not-used-in-agent-tests",
        first_name="Agent",
        last_name="Owner",
    )


def test_agent_models_constraints_relationships_and_repositories() -> None:
    async def exercise() -> None:
        async with sqlite_session() as session:
            owner = _user()
            session.add(owner)
            await session.flush()

            agents = AgentDefinitionRepository(session)
            agent = await agents.create(
                {
                    "slug": "executive-assistant",
                    "name": "Executive Assistant",
                    "description": "Operational assistant",
                    "system_instructions": "Operate safely.",
                    "status": AgentStatus.ACTIVE,
                    "version": 1,
                    "is_default": True,
                    "created_by_id": owner.id,
                }
            )
            second = await agents.create(
                {
                    "slug": "research-assistant",
                    "name": "Research Assistant",
                    "system_instructions": "Research safely.",
                    "status": AgentStatus.ACTIVE,
                    "version": 1,
                    "is_default": False,
                    "created_by_id": owner.id,
                }
            )
            selected = await agents.set_active_default(second.id)
            assert selected is second
            assert second.is_default is True
            assert agent.is_default is False
            assert await agents.get_active_default() is second

            conversation = AgentConversation(
                agent_id=second.id,
                owner_id=owner.id,
                title="Quarterly planning",
                status=ConversationStatus.ACTIVE,
                priority=ConversationPriority.HIGH,
                metadata_={"source": "test"},
            )
            session.add(conversation)
            await session.flush()
            run = AgentRun(
                conversation_id=conversation.id,
                agent_id=second.id,
                triggered_by_id=owner.id,
                status=AgentRunStatus.RUNNING,
                prompt_tokens=10,
                completion_tokens=5,
                total_tokens=15,
                estimated_cost_gbp=Decimal("0.001250"),
                latency_ms=30,
                context_snapshot={"project_ids": []},
            )
            session.add(run)
            await session.flush()

            messages = AgentMessageRepository(session)
            assert await messages.next_sequence(conversation.id) == 1
            later = AgentMessage(
                conversation_id=conversation.id,
                run_id=run.id,
                role=MessageRole.ASSISTANT,
                content="Second",
                content_type=MessageContentType.TEXT,
                sequence_number=2,
                token_count=1,
            )
            first = AgentMessage(
                conversation_id=conversation.id,
                run_id=run.id,
                role=MessageRole.USER,
                content="First",
                content_type=MessageContentType.TEXT,
                sequence_number=1,
                created_by_id=owner.id,
            )
            session.add_all([later, first])
            await session.flush()
            assert await messages.next_sequence(conversation.id) == 3

            tool = AgentToolDefinition(
                slug="projects.read",
                name="Read projects",
                description="Read projects.",
                category="projects",
                risk_level=AgentRiskLevel.LOW,
                execution_mode=ToolExecutionMode.INTERNAL,
                requires_approval=False,
                is_enabled=True,
                input_schema={"type": "object"},
                output_schema={"type": "object"},
            )
            disabled_tool = AgentToolDefinition(
                slug="memory.write",
                name="Write memory",
                description="Write memory.",
                category="memory",
                risk_level=AgentRiskLevel.HIGH,
                execution_mode=ToolExecutionMode.DEFERRED,
                requires_approval=True,
                is_enabled=False,
                input_schema={},
                output_schema={},
            )
            session.add_all([tool, disabled_tool])
            await session.flush()
            execution = AgentToolExecution(
                run_id=run.id,
                tool_id=tool.id,
                status=ToolExecutionStatus.PENDING,
                input_payload={"project_id": None},
                duration_ms=0,
            )
            session.add(execution)
            await session.flush()
            now = datetime.now(UTC)
            pending = AgentApproval(
                run_id=run.id,
                tool_execution_id=execution.id,
                requested_by_id=owner.id,
                action_type="projects.read",
                risk_level=AgentRiskLevel.LOW,
                status=AgentApprovalStatus.PENDING,
                reason="Review access",
                request_payload={},
                expires_at=now + timedelta(hours=1),
            )
            expired = AgentApproval(
                run_id=run.id,
                requested_by_id=owner.id,
                action_type="memory.write",
                risk_level=AgentRiskLevel.HIGH,
                status=AgentApprovalStatus.PENDING,
                reason="Expired request",
                request_payload={},
                expires_at=now - timedelta(hours=1),
            )
            session.add_all([pending, expired])
            await session.flush()
            execution.approval_id = pending.id

            active_memory = AgentMemory(
                owner_id=owner.id,
                agent_id=second.id,
                conversation_id=conversation.id,
                memory_type=AgentMemoryType.PREFERENCE,
                scope=AgentMemoryScope.USER,
                key="reporting.currency",
                value="GBP",
                importance=5,
                is_active=True,
                source_message_id=first.id,
                created_by_id=owner.id,
            )
            expired_memory = AgentMemory(
                owner_id=owner.id,
                memory_type=AgentMemoryType.FACT,
                scope=AgentMemoryScope.USER,
                key="expired.fact",
                value="old",
                importance=1,
                is_active=True,
                expires_at=now - timedelta(minutes=1),
            )
            inactive_memory = AgentMemory(
                owner_id=owner.id,
                memory_type=AgentMemoryType.FACT,
                scope=AgentMemoryScope.USER,
                key="inactive.fact",
                value="disabled",
                importance=1,
                is_active=False,
            )
            session.add_all(
                [active_memory, expired_memory, inactive_memory]
            )
            session.add(
                AgentContextSnapshot(
                    run_id=run.id,
                    user_context={"user_id": str(owner.id)},
                    permission_context={"roles": []},
                    project_context={},
                    task_context={},
                    crm_context={},
                    outreach_context={},
                    notification_context={},
                    memory_context={"keys": ["reporting.currency"]},
                )
            )
            session.add(
                AgentAttachment(
                    conversation_id=conversation.id,
                    message_id=first.id,
                    uploaded_by_id=owner.id,
                    filename="brief.txt",
                    content_type="text/plain",
                    size_bytes=5,
                    storage_key="agent/test/brief.txt",
                    status=AgentAttachmentStatus.AVAILABLE,
                    metadata_={},
                )
            )
            await session.commit()

            ordered = await messages.list_for_conversation(conversation.id)
            assert [item.content for item in ordered.items] == [
                "First",
                "Second",
            ]
            approvals = await AgentApprovalRepository(
                session
            ).list_pending_unexpired(now=now)
            assert [item.id for item in approvals.items] == [pending.id]
            memories = await AgentMemoryRepository(
                session
            ).list_active_unexpired(
                scope=AgentMemoryScope.USER,
                owner_id=owner.id,
                now=now,
            )
            assert [item.key for item in memories.items] == [
                "reporting.currency"
            ]
            enabled = await AgentToolDefinitionRepository(
                session
            ).list_enabled()
            assert [item.slug for item in enabled] == ["projects.read"]
            loaded = await AgentRunRepository(session).get_with_related(run.id)
            assert loaded is not None
            assert loaded.agent.slug == "research-assistant"
            assert len(loaded.tool_executions) == 1
            assert loaded.tool_executions[0].tool.slug == "projects.read"
            assert len(loaded.approvals) == 2
            assert loaded.snapshot is not None

            related = await session.scalar(
                select(AgentConversation)
                .where(AgentConversation.id == conversation.id)
                .options(
                    selectinload(AgentConversation.messages),
                    selectinload(AgentConversation.attachments),
                )
            )
            assert related is not None
            assert [item.sequence_number for item in related.messages] == [1, 2]
            assert related.attachments[0].filename == "brief.txt"

    asyncio.run(exercise())


def test_agent_database_constraints() -> None:
    async def exercise() -> None:
        async with sqlite_session() as session:
            owner = _user("constraints@example.com")
            session.add(owner)
            await session.flush()
            base = AgentDefinition(
                slug="base-agent",
                name="Base",
                system_instructions="Base",
                status=AgentStatus.ACTIVE,
                version=1,
                is_default=True,
                created_by_id=owner.id,
            )
            session.add(base)
            await session.commit()

            invalid_rows = [
                AgentDefinition(
                    slug="base-agent",
                    name="Duplicate",
                    system_instructions="Duplicate",
                    version=1,
                    created_by_id=owner.id,
                ),
                AgentDefinition(
                    slug="zero-version",
                    name="Zero",
                    system_instructions="Zero",
                    version=0,
                    created_by_id=owner.id,
                ),
                AgentDefinition(
                    slug="second-default",
                    name="Second",
                    system_instructions="Second",
                    status=AgentStatus.ACTIVE,
                    version=1,
                    is_default=True,
                    created_by_id=owner.id,
                ),
            ]
            for row in invalid_rows:
                with pytest.raises(IntegrityError):
                    async with session.begin_nested():
                        session.add(row)
                        await session.flush()

            conversation = AgentConversation(
                agent_id=base.id,
                owner_id=owner.id,
                title="Constraints",
            )
            session.add(conversation)
            await session.flush()
            run = AgentRun(
                conversation_id=conversation.id,
                agent_id=base.id,
                triggered_by_id=owner.id,
                status=AgentRunStatus.QUEUED,
            )
            session.add(run)
            await session.flush()
            session.add(
                AgentMessage(
                    conversation_id=conversation.id,
                    role=MessageRole.USER,
                    content="One",
                    sequence_number=1,
                )
            )
            await session.flush()

            constrained_rows = [
                AgentMessage(
                    conversation_id=conversation.id,
                    role=MessageRole.USER,
                    content="Duplicate",
                    sequence_number=1,
                ),
                AgentRun(
                    conversation_id=conversation.id,
                    agent_id=base.id,
                    triggered_by_id=owner.id,
                    status=AgentRunStatus.FAILED,
                    prompt_tokens=-1,
                ),
                AgentRun(
                    conversation_id=conversation.id,
                    agent_id=base.id,
                    triggered_by_id=owner.id,
                    status=AgentRunStatus.FAILED,
                    estimated_cost_gbp=Decimal("-0.000001"),
                ),
                AgentMemory(
                    owner_id=owner.id,
                    memory_type=AgentMemoryType.FACT,
                    scope=AgentMemoryScope.USER,
                    key="invalid.importance",
                    value="invalid",
                    importance=6,
                ),
            ]
            for row in constrained_rows:
                with pytest.raises(IntegrityError):
                    async with session.begin_nested():
                        session.add(row)
                        await session.flush()

            tool = AgentToolDefinition(
                slug="test.read",
                name="Test",
                description="Test",
                category="test",
                risk_level=AgentRiskLevel.LOW,
                execution_mode=ToolExecutionMode.INTERNAL,
                requires_approval=False,
                is_enabled=True,
                input_schema={},
                output_schema={},
            )
            session.add(tool)
            await session.flush()
            with pytest.raises(IntegrityError):
                async with session.begin_nested():
                    session.add(
                        AgentToolExecution(
                            run_id=run.id,
                            tool_id=tool.id,
                            status=ToolExecutionStatus.FAILED,
                            input_payload={},
                            duration_ms=-1,
                        )
                    )
                    await session.flush()

    asyncio.run(exercise())


def test_agent_migration_upgrade_constraints_indexes_and_downgrade(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "agent-migration.sqlite3"
    async_url = f"sqlite+aiosqlite:///{database_path}"
    sync_url = f"sqlite:///{database_path}"
    config = Config("alembic.ini")
    config.attributes["database_url"] = async_url

    command.upgrade(config, "20260726_0007")

    from sqlalchemy import create_engine

    engine = create_engine(sync_url)
    inspector = inspect(engine)
    agent_tables = {
        "agent_definitions",
        "agent_conversations",
        "agent_messages",
        "agent_runs",
        "agent_tool_definitions",
        "agent_tool_executions",
        "agent_approvals",
        "agent_memories",
        "agent_context_snapshots",
        "agent_attachments",
    }
    assert agent_tables.issubset(set(inspector.get_table_names()))
    assert {
        index["name"]
        for index in inspector.get_indexes("agent_conversations")
    } == {
        "ix_agent_conversations_agent_status",
        "ix_agent_conversations_owner_status_updated",
    }
    assert {
        index["name"] for index in inspector.get_indexes("agent_runs")
    } == {
        "ix_agent_runs_agent_status",
        "ix_agent_runs_conversation_status_created",
    }
    assert {
        constraint["name"]
        for constraint in inspector.get_check_constraints("agent_runs")
    } >= {
        "ck_agent_runs_agent_run_cost_nonnegative",
        "ck_agent_runs_agent_run_latency_nonnegative",
        "ck_agent_runs_agent_run_total_tokens_nonnegative",
    }
    assert {
        constraint["name"]
        for constraint in inspector.get_unique_constraints(
            "agent_messages"
        )
    } == {"uq_agent_messages_conversation_sequence"}
    engine.dispose()

    command.downgrade(config, "20260723_0006")
    engine = create_engine(sync_url)
    assert agent_tables.isdisjoint(set(inspect(engine).get_table_names()))
    engine.dispose()
