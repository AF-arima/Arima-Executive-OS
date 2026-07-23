import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.database.models import (
    Project,
    ProjectStatus,
    Task,
    TaskPriority,
    TaskStatus,
    User,
)
from app.database.repositories import AnalyticsRepository
from app.services.permissions import AnalyticsScope, VisibilityKind
from tests.database.helpers import sqlite_session


def make_user(email: str) -> User:
    return User(
        email=email,
        hashed_password="hashed",
        first_name="Analytics",
        last_name="User",
    )


def test_dashboard_repository_aggregate_and_scope_semantics() -> None:
    async def exercise() -> None:
        async with sqlite_session() as session:
            manager = make_user("repository-manager@example.com")
            other = make_user("repository-other@example.com")
            session.add_all([manager, other])
            await session.flush()
            owned = Project(
                name="Owned",
                status=ProjectStatus.ACTIVE,
                owner_id=manager.id,
                created_by=manager.id,
            )
            hidden = Project(
                name="Hidden",
                owner_id=other.id,
                created_by=other.id,
            )
            session.add_all([owned, hidden])
            await session.flush()
            now = datetime.now(timezone.utc)
            session.add_all(
                [
                    Task(
                        title="Completed",
                        status=TaskStatus.COMPLETED,
                        priority=TaskPriority.HIGH,
                        project_id=owned.id,
                        assignee_id=manager.id,
                        created_by=manager.id,
                        created_at=now - timedelta(hours=2),
                        completed_at=now,
                    ),
                    Task(
                        title="Overdue",
                        status=TaskStatus.IN_PROGRESS,
                        priority=TaskPriority.URGENT,
                        project_id=owned.id,
                        assignee_id=manager.id,
                        created_by=manager.id,
                        due_date=now - timedelta(days=1),
                    ),
                    Task(
                        title="Hidden",
                        project_id=hidden.id,
                        created_by=other.id,
                    ),
                ]
            )
            await session.commit()

            repository = AnalyticsRepository(session)
            manager_raw = await repository.dashboard(
                AnalyticsScope(
                    VisibilityKind.OWNED,
                    manager.id,
                    ("manager",),
                ),
                start=now - timedelta(days=2),
                end=now + timedelta(days=1),
                now=now,
                project_id=None,
                owner_id=None,
                assigned_to=None,
                include_archived=False,
            )
            global_raw = await repository.dashboard(
                AnalyticsScope(
                    VisibilityKind.GLOBAL,
                    other.id,
                    ("executive",),
                ),
                start=now - timedelta(days=2),
                end=now + timedelta(days=1),
                now=now,
                project_id=None,
                owner_id=None,
                assigned_to=None,
                include_archived=False,
            )

            assert manager_raw.total_projects == 1
            assert manager_raw.total_tasks == 2
            assert manager_raw.completed_tasks == 1
            assert manager_raw.overdue_tasks == 1
            assert manager_raw.average_completion_time_hours == pytest.approx(
                2
            )
            assert global_raw.total_projects == 2
            assert global_raw.total_tasks == 3

    asyncio.run(exercise())
