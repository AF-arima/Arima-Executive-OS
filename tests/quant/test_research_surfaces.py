from datetime import UTC, datetime, timedelta

import pytest

from app.database.models import Tenant, User, Workspace
from app.schemas.research import (
    DatasetCreate,
    ExperimentCreate,
    ResearchCreate,
    RunCreate,
)
from app.services.exceptions import ResourceNotFoundError
from app.services.research import ResearchAuthorizationError, ResearchService
from tests.database.helpers import sqlite_session


def _user(email: str) -> User:
    return User(
        email=email,
        hashed_password="not-used",
        first_name="Research",
        last_name="User",
        is_verified=True,
    )


@pytest.mark.asyncio
async def test_qlab_and_research_are_workspace_scoped_and_fail_closed_without_provider():
    async with sqlite_session() as session:
        first = _user("qlab-first@example.com")
        second = _user("qlab-second@example.com")
        first_workspace = Workspace(
            name="First workspace", tenant=Tenant(name="First tenant"), owner=first
        )
        second_workspace = Workspace(
            name="Second workspace", tenant=Tenant(name="Second tenant"), owner=second
        )
        session.add_all([first, second, first_workspace, second_workspace])
        await session.flush()
        service = ResearchService(session)

        experiment = await service.create_experiment(
            ExperimentCreate(name="First experiment", workspace_id=first_workspace.id),
            first,
        )
        await service.add_dataset(
            experiment.id,
            DatasetCreate(
                name="Verified input",
                source="authoritative-fixture",
                observed_at=datetime.now(UTC) - timedelta(minutes=1),
                provenance={"source": "fixture", "version": "1"},
            ),
            first,
        )
        run = await service.create_run(experiment.id, RunCreate(), first)
        records = await service.create_research(
            ResearchCreate(
                title="First note",
                content="Workspace-scoped research",
                source="founder-note",
                observed_at=datetime.now(UTC) - timedelta(minutes=1),
                provenance={"source": "user_authored"},
                workspace_id=first_workspace.id,
            ),
            first,
        )

        assert run.status == "not_configured"
        assert run.result == {}
        assert run.provenance["status"] == "not_configured"
        assert records.workspace_id == first_workspace.id
        assert len(await service.list_experiments(first)) == 1
        assert len(await service.list_research(first, "Workspace-scoped")) == 1
        with pytest.raises(ResourceNotFoundError):
            await service._experiment(second, experiment.id)
        assert await service.list_experiments(second) == []


@pytest.mark.asyncio
async def test_qlab_rejects_cross_workspace_selector_and_untrusted_timestamp():
    async with sqlite_session() as session:
        actor = _user("qlab-selector@example.com")
        other = _user("qlab-other@example.com")
        own = Workspace(
            name="Own workspace", tenant=Tenant(name="Own tenant"), owner=actor
        )
        foreign = Workspace(
            name="Foreign workspace", tenant=Tenant(name="Foreign tenant"), owner=other
        )
        session.add_all([actor, other, own, foreign])
        await session.flush()
        service = ResearchService(session)

        with pytest.raises(ResearchAuthorizationError):
            await service.create_experiment(
                ExperimentCreate(name="forged", workspace_id=foreign.id), actor
            )

        experiment = await service.create_experiment(
            ExperimentCreate(name="safe", workspace_id=own.id), actor
        )
        with pytest.raises(ResearchAuthorizationError, match="timezone-aware"):
            await service.add_dataset(
                experiment.id,
                DatasetCreate(
                    name="invalid",
                    source="fixture",
                    observed_at=datetime.now(),
                    provenance={"source": "fixture"},
                ),
                actor,
            )
        with pytest.raises(ResearchAuthorizationError, match="future"):
            await service.create_research(
                ResearchCreate(
                    title="future",
                    content="not accepted",
                    source="fixture",
                    observed_at=datetime.now(UTC) + timedelta(minutes=1),
                    provenance={"source": "fixture"},
                    workspace_id=own.id,
                ),
                actor,
            )
