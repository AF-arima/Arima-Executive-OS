import asyncio
from uuid import uuid4

from app.database.models import Project, ProjectStatus, Task, TaskPriority, TaskStatus, User
from app.execution.builders import ContextBuilder
from tests.database.helpers import sqlite_session
from tests.orchestration.helpers import make_context


def test_legacy_context_builder_excludes_cross_owner_assigned_tasks() -> None:
    """Imported inconsistent rows must not cross an AI ownership boundary."""

    async def scenario() -> None:
        async with sqlite_session() as session:
            context = await make_context(session)
            other_user = User(
                email=f"other-context-{uuid4()}@example.com",
                hashed_password="hash",
                first_name="Other",
                last_name="Owner",
                is_active=True,
                is_verified=True,
            )
            session.add(other_user)
            await session.flush()
            foreign_project = Project(
                name=f"Imported foreign project {uuid4()}",
                status=ProjectStatus.ACTIVE,
                owner_id=other_user.id,
                created_by=other_user.id,
            )
            session.add(foreign_project)
            await session.flush()
            foreign_task = Task(
                title="Foreign task assigned by legacy import",
                status=TaskStatus.TODO,
                priority=TaskPriority.MEDIUM,
                project_id=foreign_project.id,
                created_by=other_user.id,
                assignee_id=context.user.id,
            )
            session.add(foreign_task)
            await session.commit()

            built = await ContextBuilder(session).build(
                context.run.id,
                context.user,
            )
            assert str(foreign_task.id) not in {
                task["id"] for task in built.tasks
            }

    asyncio.run(scenario())
