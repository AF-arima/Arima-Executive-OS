import asyncio
from datetime import date, datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.database.models import (
    Project,
    ProjectStatus,
    Role,
    Task,
    TaskPriority,
    TaskStatus,
    User,
)
from tests.database.helpers import sqlite_session


def make_user(email: str) -> User:
    return User(
        email=email,
        hashed_password="hashed",
        first_name="Aryan",
        last_name="Heidari",
    )


def test_model_creation_and_relationships() -> None:
    async def exercise_models() -> None:
        async with sqlite_session() as session:
            owner = make_user("owner@example.com")
            role = Role(name="owner", description="Project owner")
            owner.roles.append(role)

            project = Project(
                name="Executive OS",
                owner=owner,
                status=ProjectStatus.ACTIVE,
                start_date=date(2026, 7, 23),
            )
            task = Task(
                title="Build database layer",
                project=project,
                assignee=owner,
                status=TaskStatus.IN_PROGRESS,
                priority=TaskPriority.HIGH,
                due_date=datetime(2026, 8, 1, tzinfo=timezone.utc),
            )
            session.add_all([owner, project, task])
            await session.commit()

            assert project.created_at.tzinfo is not None
            assert project.updated_at.tzinfo is not None
            assert Project.__table__.c.created_at.type.timezone is True
            assert Project.__table__.c.updated_at.type.timezone is True

            owner_id = owner.id
            project_id = project.id
            session.expunge_all()

            loaded_user = await session.scalar(
                select(User)
                .where(User.id == owner_id)
                .options(
                    selectinload(User.roles),
                    selectinload(User.owned_projects),
                    selectinload(User.assigned_tasks),
                )
            )
            loaded_project = await session.scalar(
                select(Project)
                .where(Project.id == project_id)
                .options(
                    selectinload(Project.owner),
                    selectinload(Project.tasks).selectinload(Task.assignee),
                )
            )

            assert loaded_user is not None
            assert [item.name for item in loaded_user.roles] == ["owner"]
            assert [item.id for item in loaded_user.owned_projects] == [
                project_id
            ]
            assert [item.title for item in loaded_user.assigned_tasks] == [
                "Build database layer"
            ]

            assert loaded_project is not None
            assert loaded_project.owner.id == owner_id
            assert loaded_project.status is ProjectStatus.ACTIVE
            assert [item.title for item in loaded_project.tasks] == [
                "Build database layer"
            ]
            assert loaded_project.tasks[0].assignee is not None
            assert loaded_project.tasks[0].assignee.id == owner_id
            assert loaded_project.tasks[0].priority is TaskPriority.HIGH

    asyncio.run(exercise_models())


@pytest.mark.parametrize(
    ("model_factory", "duplicate_factory"),
    [
        (
            lambda: make_user("unique@example.com"),
            lambda: make_user("unique@example.com"),
        ),
        (
            lambda: Role(name="unique-role"),
            lambda: Role(name="unique-role"),
        ),
    ],
)
def test_unique_constraints(
    model_factory: object,
    duplicate_factory: object,
) -> None:
    async def exercise_constraint() -> None:
        assert callable(model_factory)
        assert callable(duplicate_factory)

        async with sqlite_session() as session:
            session.add(model_factory())
            await session.commit()

            session.add(duplicate_factory())
            with pytest.raises(IntegrityError):
                await session.commit()
            await session.rollback()

    asyncio.run(exercise_constraint())
