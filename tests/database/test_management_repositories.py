import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.exc import IntegrityError

from app.database.models import (
    Project,
    ProjectStatus,
    Task,
    TaskPriority,
    TaskStatus,
    User,
)
from app.database.repositories import (
    ProjectFilters,
    ProjectRepository,
    TaskFilters,
    TaskRepository,
)
from app.schemas.common import SortDirection
from app.schemas.project import ProjectSortField
from app.schemas.task import TaskSortField
from tests.database.helpers import sqlite_session


def make_user(email: str) -> User:
    return User(
        email=email,
        hashed_password="hashed",
        first_name="Test",
        last_name="User",
    )


def test_project_filter_search_sort_and_pagination() -> None:
    async def exercise() -> None:
        async with sqlite_session() as session:
            owner = make_user("project-owner@example.com")
            session.add(owner)
            await session.flush()
            session.add_all(
                [
                    Project(
                        name="Alpha_100%",
                        description="Quarterly plan",
                        status=ProjectStatus.ACTIVE,
                        owner_id=owner.id,
                        created_by=owner.id,
                    ),
                    Project(
                        name="Beta",
                        description="ALPHA planning",
                        status=ProjectStatus.PLANNING,
                        owner_id=owner.id,
                        created_by=owner.id,
                    ),
                    Project(
                        name="Gamma",
                        status=ProjectStatus.ACTIVE,
                        owner_id=owner.id,
                        created_by=owner.id,
                    ),
                ]
            )
            await session.commit()

            repository = ProjectRepository(session)
            literal = await repository.list_filtered(
                ProjectFilters(search="_100%"),
                limit=10,
                offset=0,
                sort_by=ProjectSortField.NAME,
                direction=SortDirection.ASC,
            )
            active = await repository.list_filtered(
                ProjectFilters(
                    status=ProjectStatus.ACTIVE,
                    owner_id=owner.id,
                    created_by=owner.id,
                ),
                limit=1,
                offset=1,
                sort_by=ProjectSortField.NAME,
                direction=SortDirection.ASC,
            )

            assert [project.name for project in literal.items] == [
                "Alpha_100%"
            ]
            assert active.total == 2
            assert active.limit == 1
            assert active.offset == 1
            assert [project.name for project in active.items] == ["Gamma"]

    asyncio.run(exercise())


def test_task_filter_search_sort_and_completion_flags() -> None:
    async def exercise() -> None:
        async with sqlite_session() as session:
            owner = make_user("task-owner@example.com")
            assignee = make_user("task-assignee@example.com")
            session.add_all([owner, assignee])
            await session.flush()
            project = Project(
                name="Tasks",
                owner_id=owner.id,
                created_by=owner.id,
            )
            session.add(project)
            await session.flush()
            now = datetime.now(timezone.utc)
            session.add_all(
                [
                    Task(
                        title="Urgent overdue",
                        description="Literal_100%",
                        status=TaskStatus.IN_PROGRESS,
                        priority=TaskPriority.URGENT,
                        project_id=project.id,
                        assignee_id=assignee.id,
                        created_by=owner.id,
                        due_date=now - timedelta(days=1),
                    ),
                    Task(
                        title="Low complete",
                        status=TaskStatus.COMPLETED,
                        priority=TaskPriority.LOW,
                        project_id=project.id,
                        assignee_id=assignee.id,
                        created_by=owner.id,
                        completed_at=now,
                    ),
                    Task(
                        title="High future",
                        status=TaskStatus.TODO,
                        priority=TaskPriority.HIGH,
                        project_id=project.id,
                        created_by=owner.id,
                        due_date=now + timedelta(days=1),
                    ),
                ]
            )
            await session.commit()

            repository = TaskRepository(session)
            overdue = await repository.list_filtered(
                TaskFilters(
                    project_id=project.id,
                    assigned_to=assignee.id,
                    overdue=True,
                    completed=False,
                    search="_100%",
                ),
                now=now,
                limit=10,
                offset=0,
                sort_by=TaskSortField.PRIORITY,
                direction=SortDirection.DESC,
            )
            priorities = await repository.list_filtered(
                TaskFilters(project_id=project.id),
                now=now,
                limit=2,
                offset=0,
                sort_by=TaskSortField.PRIORITY,
                direction=SortDirection.DESC,
            )

            assert [task.title for task in overdue.items] == [
                "Urgent overdue"
            ]
            assert priorities.total == 3
            assert [task.priority for task in priorities.items] == [
                TaskPriority.URGENT,
                TaskPriority.HIGH,
            ]

    asyncio.run(exercise())


def test_active_project_name_constraint_is_race_safe() -> None:
    async def exercise() -> None:
        async with sqlite_session() as session:
            owner = make_user("constraint-owner@example.com")
            session.add(owner)
            await session.flush()
            owner_id = owner.id
            original = Project(
                name="Same Name",
                owner_id=owner_id,
                created_by=owner_id,
            )
            session.add(original)
            await session.commit()

            session.add(
                Project(
                    name="same name",
                    owner_id=owner_id,
                    created_by=owner_id,
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()

            original.archived_at = datetime.now(timezone.utc)
            await session.commit()
            session.add(
                Project(
                    name="SAME NAME",
                    owner_id=owner_id,
                    created_by=owner_id,
                )
            )
            await session.commit()

    asyncio.run(exercise())
