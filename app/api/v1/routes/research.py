from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.v1.dependencies import SessionDependency
from app.auth.dependencies import get_current_active_user
from app.database.models import User
from app.schemas.research import (
    DatasetCreate,
    DatasetRead,
    ExperimentCreate,
    ExperimentRead,
    ModelCreate,
    ModelRead,
    ResearchCreate,
    ResearchRead,
    RunCreate,
    RunRead,
)
from app.services.exceptions import ResourceNotFoundError
from app.services.research import ResearchAuthorizationError, ResearchService

router = APIRouter(prefix="/research", tags=["research"])
CurrentUser = Annotated[User, Depends(get_current_active_user)]


def _http(error: Exception) -> HTTPException:
    code = 403 if isinstance(error, ResearchAuthorizationError) else 404
    return HTTPException(status_code=code, detail=str(error))


def _experiment_read(item):
    return ExperimentRead.model_validate(item, from_attributes=True)


def _dataset_read(item):
    return DatasetRead(
        id=item.id,
        experiment_id=item.experiment_id,
        workspace_id=item.workspace_id,
        account_id=item.account_id,
        name=item.name,
        source=item.source,
        observed_at=item.observed_at,
        status=item.status,
        provenance=item.provenance,
        metadata=item.metadata_json,
        created_at=item.created_at,
    )


def _model_read(item):
    return ModelRead.model_validate(item, from_attributes=True)


def _run_read(item):
    return RunRead.model_validate(item, from_attributes=True)


def _research_read(item):
    return ResearchRead.model_validate(item, from_attributes=True)


@router.post(
    "/qlab/experiments",
    response_model=ExperimentRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_experiment(
    data: ExperimentCreate, session: SessionDependency, actor: CurrentUser
) -> ExperimentRead:
    try:
        return _experiment_read(
            await ResearchService(session).create_experiment(data, actor)
        )
    except (ResearchAuthorizationError, ResourceNotFoundError) as error:
        raise _http(error) from error


@router.get("/qlab/experiments", response_model=list[ExperimentRead])
async def list_experiments(
    session: SessionDependency, actor: CurrentUser
) -> list[ExperimentRead]:
    try:
        return [
            _experiment_read(item)
            for item in await ResearchService(session).list_experiments(actor)
        ]
    except ResearchAuthorizationError as error:
        raise _http(error) from error


@router.post(
    "/qlab/experiments/{experiment_id}/datasets",
    response_model=DatasetRead,
    status_code=status.HTTP_201_CREATED,
)
async def add_dataset(
    experiment_id: UUID,
    data: DatasetCreate,
    session: SessionDependency,
    actor: CurrentUser,
) -> DatasetRead:
    try:
        return _dataset_read(
            await ResearchService(session).add_dataset(experiment_id, data, actor)
        )
    except (ResearchAuthorizationError, ResourceNotFoundError) as error:
        raise _http(error) from error


@router.post(
    "/qlab/experiments/{experiment_id}/models",
    response_model=ModelRead,
    status_code=status.HTTP_201_CREATED,
)
async def add_model(
    experiment_id: UUID,
    data: ModelCreate,
    session: SessionDependency,
    actor: CurrentUser,
) -> ModelRead:
    try:
        return _model_read(
            await ResearchService(session).add_model(experiment_id, data, actor)
        )
    except (ResearchAuthorizationError, ResourceNotFoundError) as error:
        raise _http(error) from error


@router.get("/qlab/experiments/{experiment_id}/runs", response_model=list[RunRead])
async def list_runs(
    experiment_id: UUID, session: SessionDependency, actor: CurrentUser
) -> list[RunRead]:
    try:
        runs = (await ResearchService(session).list_related(experiment_id, actor))[2]
        return [_run_read(item) for item in runs]
    except (ResearchAuthorizationError, ResourceNotFoundError) as error:
        raise _http(error) from error


@router.post(
    "/qlab/experiments/{experiment_id}/runs",
    response_model=RunRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_run(
    experiment_id: UUID, data: RunCreate, session: SessionDependency, actor: CurrentUser
) -> RunRead:
    try:
        return _run_read(
            await ResearchService(session).create_run(experiment_id, data, actor)
        )
    except (ResearchAuthorizationError, ResourceNotFoundError) as error:
        raise _http(error) from error


@router.post("", response_model=ResearchRead, status_code=status.HTTP_201_CREATED)
async def create_research(
    data: ResearchCreate, session: SessionDependency, actor: CurrentUser
) -> ResearchRead:
    try:
        return _research_read(
            await ResearchService(session).create_research(data, actor)
        )
    except ResearchAuthorizationError as error:
        raise _http(error) from error


@router.get("", response_model=list[ResearchRead])
async def list_research(
    session: SessionDependency,
    actor: CurrentUser,
    query: str | None = Query(default=None, max_length=200),
) -> list[ResearchRead]:
    try:
        return [
            _research_read(item)
            for item in await ResearchService(session).list_research(actor, query)
        ]
    except ResearchAuthorizationError as error:
        raise _http(error) from error
