import asyncio
from datetime import timedelta
from uuid import uuid4

from app.background.schemas import BackgroundJobState, ScheduleType
from app.database.models import (
    AgentApproval,
    AgentApprovalStatus,
    AgentMemory,
    AgentMemoryScope,
    AgentMemoryType,
    AgentRiskLevel,
    AuditAction,
    AuditEntity,
    AuditLog,
    BackgroundJobDefinition,
    BackgroundJobSchedule,
    Notification,
    NotificationType,
    Project,
    ProjectStatus,
    Task,
    TaskPriority,
    TaskStatus,
    User,
)
from app.orchestration.factory import OrchestrationFactory
from app.orchestration.schemas import OrchestrationRequest
from tests.database.helpers import sqlite_session
from tests.orchestration.helpers import NOW, make_context


def test_focus_question_uses_bounded_persisted_executive_state() -> None:
    async def scenario() -> None:
        async with sqlite_session() as session:
            context = await make_context(
                session,
                OrchestrationRequest(content="What should I focus on today?"),
            )
            project = Project(
                name="Persisted priority project",
                status=ProjectStatus.ACTIVE,
                owner_id=context.user.id,
                created_by=context.user.id,
            )
            session.add(project)
            await session.flush()
            session.add_all(
                [
                    Task(
                        title="Urgent persisted task",
                        status=TaskStatus.TODO,
                        priority=TaskPriority.URGENT,
                        due_date=NOW - timedelta(hours=1),
                        project_id=project.id,
                        created_by=context.user.id,
                    ),
                    Task(
                        title="High task due today",
                        status=TaskStatus.IN_PROGRESS,
                        priority=TaskPriority.HIGH,
                        due_date=NOW + timedelta(hours=2),
                        project_id=project.id,
                        created_by=context.user.id,
                    ),
                    Notification(
                        user_id=context.user.id,
                        type=NotificationType.TASK_OVERDUE,
                        title="Persisted notification",
                        message="A persisted task is overdue.",
                    ),
                    AgentMemory(
                        owner_id=context.user.id,
                        agent_id=context.agent.id,
                        conversation_id=context.conversation.id,
                        memory_type=AgentMemoryType.DECISION,
                        scope=AgentMemoryScope.CONVERSATION,
                        key="executive-context",
                        value="Persisted conversation memory",
                        importance=5,
                        is_active=True,
                    ),
                    AgentApproval(
                        run_id=context.run.id,
                        requested_by_id=context.user.id,
                        action_type="persisted_action",
                        risk_level=AgentRiskLevel.MEDIUM,
                        status=AgentApprovalStatus.PENDING,
                        reason="Persisted approval",
                        request_payload={},
                    ),
                    AuditLog(
                        actor_id=context.user.id,
                        action=AuditAction.UPDATE,
                        entity=AuditEntity.TASK,
                        entity_id=project.id,
                        project_id=project.id,
                        timestamp=NOW,
                    ),
                ]
            )
            definition = BackgroundJobDefinition(
                job_name="persisted_research",
                version="1",
                description="Persisted research",
                category="research",
                job_type="scheduled",
                required_permissions=[],
                approval_policy="none",
                capabilities=[],
                input_schema={},
                output_schema={},
                enabled=True,
            )
            session.add(definition)
            await session.flush()
            session.add(
                BackgroundJobSchedule(
                    job_definition_id=definition.id,
                    job_name="persisted_research",
                    user_id=context.user.id,
                    agent_id=context.agent.id,
                    conversation_id=context.conversation.id,
                    run_id=context.run.id,
                    schedule_type=ScheduleType.ONE_TIME,
                    status=BackgroundJobState.SCHEDULED,
                    definition={},
                    timezone="UTC",
                    next_run_at=NOW + timedelta(hours=3),
                    enabled=True,
                    paused=False,
                    run_count=0,
                )
            )
            await session.commit()

            result = await OrchestrationFactory(session).create().execute(context)

            assert "EXECUTIVE STATE" in result.final_response
            assert "Urgent persisted task" in result.final_response
            assert "High task due today" in result.final_response
            assert "Persisted priority project" in result.final_response
            assert "persisted_action" in result.final_response
            assert "persisted_research" in result.final_response
            assert "Persisted notification" in result.final_response
            assert "updated task" in result.final_response
            assert "Persisted conversation memory" in result.final_response
            assert "No persisted decision record available." in result.final_response
            assert "Portfolio state unavailable." in result.final_response

    asyncio.run(scenario())


def test_normal_conversation_does_not_resolve_executive_state() -> None:
    async def scenario() -> None:
        async with sqlite_session() as session:
            context = await make_context(
                session, OrchestrationRequest(content="Tell me a joke")
            )
            builder = OrchestrationFactory(session).create().pipeline.context_builder

            assert await builder.resolve_executive_state(context) is None

    asyncio.run(scenario())


def test_executive_state_excludes_other_users_persisted_data() -> None:
    async def scenario() -> None:
        async with sqlite_session() as session:
            context = await make_context(
                session,
                OrchestrationRequest(content="What needs my attention?"),
            )
            other = User(
                email=f"other-{uuid4()}@example.com",
                hashed_password="hash",
                first_name="Other",
                last_name="User",
                is_active=True,
                is_verified=True,
            )
            session.add(other)
            await session.flush()
            other_project = Project(
                name="Other user project",
                status=ProjectStatus.ACTIVE,
                owner_id=other.id,
                created_by=other.id,
            )
            session.add(other_project)
            await session.flush()
            session.add_all(
                [
                    Task(
                        title="Other user urgent task",
                        status=TaskStatus.TODO,
                        priority=TaskPriority.URGENT,
                        project_id=other_project.id,
                        created_by=other.id,
                    ),
                    Notification(
                        user_id=other.id,
                        type=NotificationType.SYSTEM,
                        title="Other user notification",
                        message="Do not expose me.",
                    ),
                ]
            )
            await session.commit()

            result = await OrchestrationFactory(session).create().execute(context)

            assert "Other user project" not in result.final_response
            assert "Other user urgent task" not in result.final_response
            assert "Other user notification" not in result.final_response

    asyncio.run(scenario())
