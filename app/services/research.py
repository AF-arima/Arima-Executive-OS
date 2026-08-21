from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    AuditAction,
    AuditEntity,
    QLabDataset,
    QLabExperiment,
    QLabModel,
    QLabRun,
    ResearchRecord,
    User,
    Workspace,
    WorkspaceMembership,
)
from app.schemas.research import (
    DatasetCreate,
    ExperimentCreate,
    ModelCreate,
    ResearchCreate,
    RunCreate,
)
from app.services.audit import record_audit
from app.services.exceptions import ResourceNotFoundError
from app.services.identity import FinancialContextError, FinancialContextResolver


class ResearchAuthorizationError(PermissionError):
    pass


class ResearchService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def _workspace(
        self, actor: User, requested: UUID | None
    ) -> tuple[UUID, UUID, UUID]:
        if requested is None:
            statement = (
                select(Workspace)
                .outerjoin(
                    WorkspaceMembership,
                    WorkspaceMembership.workspace_id == Workspace.id,
                )
                .where(
                    or_(
                        Workspace.owner_id == actor.id,
                        WorkspaceMembership.user_id == actor.id,
                    )
                )
                .order_by(Workspace.created_at)
            )
            workspaces = list((await self.session.scalars(statement)).all())
            if len(workspaces) != 1:
                raise ResearchAuthorizationError(
                    "Authorized workspace selection is ambiguous or unavailable"
                )
            workspace = workspaces[0]
        else:
            workspace = await self.session.get(Workspace, requested)
            if workspace is None:
                raise ResearchAuthorizationError("Authorized workspace is unavailable")
        if workspace.tenant_id is None:
            raise ResearchAuthorizationError(
                "Authorized tenant/workspace relationship is unavailable"
            )
        try:
            context = await FinancialContextResolver(self.session).resolve(
                actor=actor, workspace_id=workspace.id, account_id=actor.id
            )
        except FinancialContextError as error:
            raise ResearchAuthorizationError(str(error)) from error
        return context.tenant_id, context.workspace_id, context.account_id

    async def _experiment(self, actor: User, experiment_id: UUID) -> QLabExperiment:
        tenant_id, workspace_id, account_id = await self._workspace(actor, None)
        experiment = await self.session.scalar(
            select(QLabExperiment).where(
                QLabExperiment.id == experiment_id,
                QLabExperiment.tenant_id == tenant_id,
                QLabExperiment.workspace_id == workspace_id,
                QLabExperiment.account_id == account_id,
            )
        )
        if experiment is None:
            raise ResourceNotFoundError("Experiment not found")
        return experiment

    @staticmethod
    def _require_timestamp(value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ResearchAuthorizationError(
                "Research timestamps must be timezone-aware"
            )
        if value > datetime.now(UTC):
            raise ResearchAuthorizationError(
                "Research timestamp cannot be in the future"
            )
        return value.astimezone(UTC)

    async def create_experiment(
        self, data: ExperimentCreate, actor: User
    ) -> QLabExperiment:
        tenant_id, workspace_id, account_id = await self._workspace(
            actor, data.workspace_id
        )
        item = QLabExperiment(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            account_id=account_id,
            created_by_id=actor.id,
            name=data.name.strip(),
            description=data.description,
            status="active",
            provenance={
                "source": "user_authored",
                "actor_id": str(actor.id),
                "recorded_at": datetime.now(UTC).isoformat(),
            },
        )
        self.session.add(item)
        await self.session.flush()
        record_audit(
            self.session,
            actor_id=actor.id,
            action=AuditAction.CREATE,
            entity=AuditEntity.ACCOUNT,
            entity_id=item.id,
            event_type="QLAB_EXPERIMENT_CREATED",
            event_metadata={"workspace_id": str(workspace_id)},
        )
        await self.session.commit()
        return item

    async def list_experiments(self, actor: User) -> list[QLabExperiment]:
        tenant_id, workspace_id, account_id = await self._workspace(actor, None)
        items = list(
            (
                await self.session.scalars(
                    select(QLabExperiment)
                    .where(
                        QLabExperiment.tenant_id == tenant_id,
                        QLabExperiment.workspace_id == workspace_id,
                        QLabExperiment.account_id == account_id,
                    )
                    .order_by(QLabExperiment.created_at.desc())
                )
            ).all()
        )
        record_audit(
            self.session,
            actor_id=actor.id,
            action=AuditAction.READ,
            entity=AuditEntity.ACCOUNT,
            entity_id=workspace_id,
            event_type="QLAB_EXPERIMENTS_READ",
        )
        await self.session.commit()
        return items

    async def add_dataset(
        self, experiment_id: UUID, data: DatasetCreate, actor: User
    ) -> QLabDataset:
        experiment = await self._experiment(actor, experiment_id)
        item = QLabDataset(
            tenant_id=experiment.tenant_id,
            workspace_id=experiment.workspace_id,
            account_id=experiment.account_id,
            experiment_id=experiment.id,
            created_by_id=actor.id,
            name=data.name.strip(),
            source=data.source.strip(),
            observed_at=self._require_timestamp(data.observed_at),
            status="unverified",
            provenance=data.provenance,
            metadata_json=data.metadata,
        )
        self.session.add(item)
        await self.session.flush()
        await self.session.commit()
        return item

    async def add_model(
        self, experiment_id: UUID, data: ModelCreate, actor: User
    ) -> QLabModel:
        experiment = await self._experiment(actor, experiment_id)
        item = QLabModel(
            tenant_id=experiment.tenant_id,
            workspace_id=experiment.workspace_id,
            account_id=experiment.account_id,
            experiment_id=experiment.id,
            created_by_id=actor.id,
            name=data.name.strip(),
            version=data.version.strip(),
            status="unverified",
            provenance=data.provenance,
        )
        self.session.add(item)
        await self.session.flush()
        await self.session.commit()
        return item

    async def list_related(
        self, experiment_id: UUID, actor: User
    ) -> tuple[list[QLabDataset], list[QLabModel], list[QLabRun]]:
        experiment = await self._experiment(actor, experiment_id)
        _, _, account_id = await self._workspace(actor, None)
        filters = (
            QLabDataset.experiment_id == experiment.id,
            QLabDataset.workspace_id == experiment.workspace_id,
            QLabDataset.account_id == account_id,
        )
        datasets = list(
            (
                await self.session.scalars(
                    select(QLabDataset)
                    .where(*filters)
                    .order_by(QLabDataset.created_at.desc())
                )
            ).all()
        )
        models = list(
            (
                await self.session.scalars(
                    select(QLabModel)
                    .where(
                        QLabModel.experiment_id == experiment.id,
                        QLabModel.workspace_id == experiment.workspace_id,
                        QLabModel.account_id == account_id,
                    )
                    .order_by(QLabModel.created_at.desc())
                )
            ).all()
        )
        runs = list(
            (
                await self.session.scalars(
                    select(QLabRun)
                    .where(
                        QLabRun.experiment_id == experiment.id,
                        QLabRun.workspace_id == experiment.workspace_id,
                        QLabRun.account_id == account_id,
                    )
                    .order_by(QLabRun.created_at.desc())
                )
            ).all()
        )
        return datasets, models, runs

    async def create_run(
        self, experiment_id: UUID, data: RunCreate, actor: User
    ) -> QLabRun:
        experiment = await self._experiment(actor, experiment_id)
        for model, model_id in (
            (QLabDataset, data.dataset_id),
            (QLabModel, data.model_id),
        ):
            if (
                model_id is not None
                and await self.session.scalar(
                    select(model.id).where(
                        model.id == model_id,
                        model.experiment_id == experiment.id,
                        model.workspace_id == experiment.workspace_id,
                        model.account_id == experiment.account_id,
                    )
                )
                is None
            ):
                raise ResourceNotFoundError("QLab input is not part of this experiment")
        item = QLabRun(
            tenant_id=experiment.tenant_id,
            workspace_id=experiment.workspace_id,
            account_id=experiment.account_id,
            experiment_id=experiment.id,
            dataset_id=data.dataset_id,
            model_id=data.model_id,
            created_by_id=actor.id,
            status="not_configured",
            result={},
            provenance={"source": "qlab_provider", "status": "not_configured"},
            failure_reason="No approved QLab research provider is configured",
        )
        self.session.add(item)
        await self.session.flush()
        await self.session.commit()
        return item

    async def create_research(
        self, data: ResearchCreate, actor: User
    ) -> ResearchRecord:
        tenant_id, workspace_id, account_id = await self._workspace(
            actor, data.workspace_id
        )
        item = ResearchRecord(
            tenant_id=tenant_id,
            workspace_id=workspace_id,
            account_id=account_id,
            created_by_id=actor.id,
            title=data.title.strip(),
            content=data.content,
            source=data.source.strip(),
            observed_at=self._require_timestamp(data.observed_at),
            status="unverified",
            provenance=data.provenance,
            tags=[tag.strip() for tag in data.tags if tag.strip()],
        )
        self.session.add(item)
        await self.session.flush()
        record_audit(
            self.session,
            actor_id=actor.id,
            action=AuditAction.CREATE,
            entity=AuditEntity.ACCOUNT,
            entity_id=item.id,
            event_type="RESEARCH_RECORD_SAVED",
            event_metadata={"workspace_id": str(workspace_id), "status": "unverified"},
        )
        await self.session.commit()
        return item

    async def list_research(
        self, actor: User, query: str | None = None
    ) -> list[ResearchRecord]:
        tenant_id, workspace_id, account_id = await self._workspace(actor, None)
        statement = select(ResearchRecord).where(
            ResearchRecord.tenant_id == tenant_id,
            ResearchRecord.workspace_id == workspace_id,
            ResearchRecord.account_id == account_id,
        )
        if query and query.strip():
            pattern = f"%{query.strip()}%"
            statement = statement.where(
                or_(
                    ResearchRecord.title.ilike(pattern),
                    ResearchRecord.content.ilike(pattern),
                    ResearchRecord.source.ilike(pattern),
                )
            )
        items = list(
            (
                await self.session.scalars(
                    statement.order_by(ResearchRecord.created_at.desc()).limit(100)
                )
            ).all()
        )
        record_audit(
            self.session,
            actor_id=actor.id,
            action=AuditAction.READ,
            entity=AuditEntity.ACCOUNT,
            entity_id=workspace_id,
            event_type="RESEARCH_RECORDS_READ",
        )
        await self.session.commit()
        return items
