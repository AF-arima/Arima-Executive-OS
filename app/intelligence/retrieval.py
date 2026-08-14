from __future__ import annotations

from datetime import UTC, datetime, timedelta
import re
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    AIRetrievedContext,
    AIWorkspaceRun,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeDocumentStatus,
    KnowledgeSource,
    User,
)
from app.intelligence.access import (
    IntelligenceAccessError,
    require_workspace_membership,
)
from app.intelligence.ingestion import _as_utc
from app.intelligence.schemas import RetrievalQuery, RetrievedKnowledge


def _terms(value: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{2,}", value.lower()))


class TenantSafeRetrievalService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def retrieve(
        self,
        *,
        workspace_id: UUID,
        run_id: UUID,
        actor: User,
        query: RetrievalQuery,
        now: datetime | None = None,
    ) -> tuple[RetrievedKnowledge, ...]:
        await require_workspace_membership(self.session, actor, workspace_id)
        binding = await self.session.scalar(
            select(AIWorkspaceRun).where(
                AIWorkspaceRun.workspace_id == workspace_id,
                AIWorkspaceRun.run_id == run_id,
                AIWorkspaceRun.user_id == actor.id,
            )
        )
        if binding is None:
            raise IntelligenceAccessError("AI run is not owned by this workspace")

        rows = (
            await self.session.execute(
                select(KnowledgeChunk, KnowledgeDocument, KnowledgeSource)
                .join(
                    KnowledgeDocument,
                    KnowledgeDocument.id == KnowledgeChunk.document_id,
                )
                .join(
                    KnowledgeSource, KnowledgeSource.id == KnowledgeDocument.source_id
                )
                .where(
                    KnowledgeChunk.workspace_id == workspace_id,
                    KnowledgeDocument.workspace_id == workspace_id,
                    KnowledgeSource.workspace_id == workspace_id,
                    KnowledgeDocument.status == KnowledgeDocumentStatus.INGESTED,
                    KnowledgeSource.is_enabled.is_(True),
                )
            )
        ).all()
        query_terms = _terms(query.text)
        checked_at = _as_utc(now or datetime.now(UTC))
        candidates: list[
            tuple[float, KnowledgeChunk, KnowledgeDocument, KnowledgeSource]
        ] = []
        for chunk, document, source in rows:
            if not document.provenance:
                continue
            if self._is_stale(document, source, checked_at):
                continue
            if (
                query.require_fresh
                and not source.freshness_required
                and document.expires_at is None
            ):
                continue
            overlap = len(query_terms.intersection(_terms(chunk.content)))
            if overlap:
                candidates.append(
                    (overlap / max(1, len(query_terms)), chunk, document, source)
                )
        candidates.sort(key=lambda item: (-item[0], item[1].ordinal))

        results: list[RetrievedKnowledge] = []
        for rank, (score, chunk, document, source) in enumerate(
            candidates[: query.limit], start=1
        ):
            evidence = AIRetrievedContext(
                workspace_id=workspace_id,
                run_id=run_id,
                chunk_id=chunk.id,
                rank=rank,
                score=score,
                source_observed_at=document.source_observed_at,
                provenance=document.provenance,
            )
            self.session.add(evidence)
            await self.session.flush()
            results.append(
                RetrievedKnowledge(
                    evidence_id=evidence.id,
                    chunk_id=chunk.id,
                    document_id=document.id,
                    source_id=source.id,
                    content=chunk.content,
                    rank=rank,
                    score=score,
                    source_observed_at=_as_utc(document.source_observed_at),
                    provenance=document.provenance,
                )
            )
        await self.session.commit()
        return tuple(results)

    @staticmethod
    def _is_stale(
        document: KnowledgeDocument,
        source: KnowledgeSource,
        now: datetime,
    ) -> bool:
        observed_at = _as_utc(document.source_observed_at)
        if document.expires_at is not None and _as_utc(document.expires_at) <= now:
            return True
        if source.freshness_required:
            if source.max_age_seconds is None:
                return True
            return observed_at + timedelta(seconds=source.max_age_seconds) <= now
        return False
