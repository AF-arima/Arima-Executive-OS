import asyncio

from app.database.models import Project, Role, Task, User
from app.database.repositories import (
    ProjectRepository,
    RoleRepository,
    TaskRepository,
    UserRepository,
)
from tests.database.helpers import sqlite_session


def make_user(email: str) -> User:
    return User(
        email=email,
        hashed_password="hashed",
        first_name="Test",
        last_name="User",
    )


def test_domain_repository_queries() -> None:
    async def exercise_repositories() -> None:
        async with sqlite_session() as session:
            owner = make_user("owner@example.com")
            assignee = make_user("assignee@example.com")
            other_user = make_user("other@example.com")
            role = Role(name="administrator")
            project = Project(name="Owned project", owner=owner)
            other_project = Project(name="Other project", owner=other_user)
            assigned_task = Task(
                title="Assigned task",
                project=project,
                assignee=assignee,
            )
            unassigned_task = Task(
                title="Unassigned task",
                project=project,
            )
            other_task = Task(
                title="Other task",
                project=other_project,
                assignee=other_user,
            )
            session.add_all(
                [
                    owner,
                    assignee,
                    other_user,
                    role,
                    project,
                    other_project,
                    assigned_task,
                    unassigned_task,
                    other_task,
                ]
            )
            await session.commit()

            users = UserRepository(session)
            roles = RoleRepository(session)
            projects = ProjectRepository(session)
            tasks = TaskRepository(session)

            assert await users.get_by_email(owner.email) is owner
            assert await users.get_by_email("missing@example.com") is None
            assert await roles.get_by_name(role.name) is role
            assert await roles.get_by_name("missing") is None

            owned_projects = await projects.list_by_owner(owner.id)
            project_tasks = await tasks.list_by_project(project.id)
            assigned_tasks = await tasks.list_by_assignee(assignee.id)

            assert list(owned_projects) == [project]
            assert set(project_tasks) == {assigned_task, unassigned_task}
            assert list(assigned_tasks) == [assigned_task]

    asyncio.run(exercise_repositories())
