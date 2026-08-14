from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.database.models import (
    AIRetrievedContext,
    KnowledgeChunk,
    WorkspaceMembership,
)
from app.intelligence.access import (
    AgentGrantService,
    IntelligenceAccessError,
    RunBindingService,
)
from app.intelligence.ingestion import (
    KnowledgeIngestionService,
    KnowledgeValidationError,
)
from app.intelligence.retrieval import TenantSafeRetrievalService
from app.intelligence.schemas import (
    KnowledgeDocumentInput,
    KnowledgeSourceInput,
    RetrievalQuery,
)
from tests.database.helpers import sqlite_session
from tests.intelligence.helpers import make_intelligence_context


@pytest.mark.asyncio
async def test_run_binding_enforces_workspace_owner_and_agent_grant() -> None:
    async with sqlite_session() as session:
        owner = await make_intelligence_context(session)
        attacker = await make_intelligence_context(session)
        await AgentGrantService(session).grant(
            workspace_id=owner.workspace.id,
            agent_id=owner.agent.id,
            actor=owner.user,
        )
        binding = await RunBindingService(session).bind(
            workspace_id=owner.workspace.id,
            run=owner.run,
            actor=owner.user,
            channel="test",
        )

        assert binding.user_id == owner.user.id
        with pytest.raises(IntelligenceAccessError):
            await RunBindingService(session).bind(
                workspace_id=owner.workspace.id,
                run=attacker.run,
                actor=attacker.user,
                channel="attack",
            )


@pytest.mark.asyncio
async def test_run_binding_rejects_missing_grant_inactive_user_and_workspace_crossing() -> (
    None
):
    async with sqlite_session() as session:
        owner = await make_intelligence_context(session)
        other = await make_intelligence_context(session)

        with pytest.raises(IntelligenceAccessError):
            await RunBindingService(session).bind(
                workspace_id=owner.workspace.id,
                run=owner.run,
                actor=owner.user,
                channel="missing-grant",
            )

        await AgentGrantService(session).grant(
            workspace_id=owner.workspace.id,
            agent_id=owner.agent.id,
            actor=owner.user,
        )
        owner.user.is_active = False
        await session.commit()
        with pytest.raises(IntelligenceAccessError):
            await RunBindingService(session).bind(
                workspace_id=owner.workspace.id,
                run=owner.run,
                actor=owner.user,
                channel="inactive",
            )

        owner.user.is_active = True
        session.add(
            WorkspaceMembership(
                workspace_id=other.workspace.id,
                user_id=owner.user.id,
                role="member",
            )
        )
        await session.commit()
        with pytest.raises(
            IntelligenceAccessError,
            match="Conversation is not bound to this workspace",
        ):
            await RunBindingService(session).bind(
                workspace_id=other.workspace.id,
                run=owner.run,
                actor=owner.user,
                channel="cross-workspace",
            )


@pytest.mark.asyncio
async def test_retrieval_is_tenant_scoped_and_records_provenance() -> None:
    async with sqlite_session() as session:
        owner = await make_intelligence_context(session)
        attacker = await make_intelligence_context(session)
        for context in (owner, attacker):
            await AgentGrantService(session).grant(
                workspace_id=context.workspace.id,
                agent_id=context.agent.id,
                actor=context.user,
            )
            await RunBindingService(session).bind(
                workspace_id=context.workspace.id,
                run=context.run,
                actor=context.user,
                channel="test",
            )
        ingestion = KnowledgeIngestionService(session)
        now = datetime.now(UTC)
        owner_source = await ingestion.create_source(
            workspace_id=owner.workspace.id,
            actor=owner.user,
            data=KnowledgeSourceInput(
                source_type="workspace",
                external_id="board-pack",
                name="Board pack",
                freshness_required=True,
                max_age_seconds=3600,
            ),
        )
        await ingestion.ingest(
            workspace_id=owner.workspace.id,
            source_id=owner_source.id,
            actor=owner.user,
            data=KnowledgeDocumentInput(
                external_id="today",
                title="Today",
                content="Priority launch decision is approved.",
                source_observed_at=now,
                provenance={"source": "board-pack", "document": "today"},
            ),
        )
        attacker_source = await ingestion.create_source(
            workspace_id=attacker.workspace.id,
            actor=attacker.user,
            data=KnowledgeSourceInput(
                source_type="workspace",
                external_id="private",
                name="Private",
            ),
        )
        attacker_ingested = await ingestion.ingest(
            workspace_id=attacker.workspace.id,
            source_id=attacker_source.id,
            actor=attacker.user,
            data=KnowledgeDocumentInput(
                external_id="secret",
                title="Secret",
                content="Priority launch decision contains attacker-only data.",
                source_observed_at=now,
                provenance={"source": "private"},
            ),
        )

        results = await TenantSafeRetrievalService(session).retrieve(
            workspace_id=owner.workspace.id,
            run_id=owner.run.id,
            actor=owner.user,
            query=RetrievalQuery(text="priority launch decision"),
            now=now,
        )

        assert len(results) == 1
        assert results[0].provenance["source"] == "board-pack"
        assert results[0].chunk_id not in attacker_ingested.chunk_ids
        assert (
            await session.scalar(
                select(func.count(AIRetrievedContext.id)).where(
                    AIRetrievedContext.run_id == owner.run.id
                )
            )
            == 1
        )
        with pytest.raises(IntelligenceAccessError):
            await TenantSafeRetrievalService(session).retrieve(
                workspace_id=owner.workspace.id,
                run_id=owner.run.id,
                actor=attacker.user,
                query=RetrievalQuery(text="priority"),
            )


@pytest.mark.asyncio
async def test_freshness_and_provenance_fail_closed() -> None:
    async with sqlite_session() as session:
        context = await make_intelligence_context(session)
        await AgentGrantService(session).grant(
            workspace_id=context.workspace.id,
            agent_id=context.agent.id,
            actor=context.user,
        )
        await RunBindingService(session).bind(
            workspace_id=context.workspace.id,
            run=context.run,
            actor=context.user,
            channel="test",
        )
        ingestion = KnowledgeIngestionService(session)
        with pytest.raises(KnowledgeValidationError):
            await ingestion.create_source(
                workspace_id=context.workspace.id,
                actor=context.user,
                data=KnowledgeSourceInput(
                    source_type="provider",
                    external_id="unsafe",
                    name="Unsafe",
                    source_uri="https://example.com/data?api_key=must-not-store",
                ),
            )
        source = await ingestion.create_source(
            workspace_id=context.workspace.id,
            actor=context.user,
            data=KnowledgeSourceInput(
                source_type="approved",
                external_id="fresh",
                name="Fresh source",
                freshness_required=True,
                max_age_seconds=60,
            ),
        )
        observed = datetime.now(UTC) - timedelta(minutes=5)
        with pytest.raises(KnowledgeValidationError):
            await ingestion.ingest(
                workspace_id=context.workspace.id,
                source_id=source.id,
                actor=context.user,
                data=KnowledgeDocumentInput(
                    external_id="missing-provenance",
                    title="Invalid",
                    content="Invalid source content",
                    source_observed_at=observed,
                    provenance={},
                ),
            )
        with pytest.raises(KnowledgeValidationError):
            await ingestion.ingest(
                workspace_id=context.workspace.id,
                source_id=source.id,
                actor=context.user,
                data=KnowledgeDocumentInput(
                    external_id="blank-provenance",
                    title="Invalid blank provenance",
                    content="Invalid source content",
                    source_observed_at=observed,
                    provenance={"source": "   "},
                ),
            )
        with pytest.raises(KnowledgeValidationError):
            await ingestion.ingest(
                workspace_id=context.workspace.id,
                source_id=source.id,
                actor=context.user,
                data=KnowledgeDocumentInput(
                    external_id="nested-credential",
                    title="Invalid nested credential",
                    content="Invalid source content",
                    source_observed_at=observed,
                    provenance={
                        "source": "approved",
                        "metadata": {"authorization": "Bearer private"},
                    },
                ),
            )
        ingested = await ingestion.ingest(
            workspace_id=context.workspace.id,
            source_id=source.id,
            actor=context.user,
            data=KnowledgeDocumentInput(
                external_id="stale",
                title="Stale",
                content="Stale executive priority",
                source_observed_at=observed,
                provenance={"source": "approved"},
            ),
        )

        results = await TenantSafeRetrievalService(session).retrieve(
            workspace_id=context.workspace.id,
            run_id=context.run.id,
            actor=context.user,
            query=RetrievalQuery(text="executive priority"),
        )

        assert results == ()
        bypass_attempt = await TenantSafeRetrievalService(session).retrieve(
            workspace_id=context.workspace.id,
            run_id=context.run.id,
            actor=context.user,
            query=RetrievalQuery(text="executive priority", require_fresh=False),
        )
        assert bypass_attempt == ()
        assert (
            await session.scalar(
                select(func.count(KnowledgeChunk.id)).where(
                    KnowledgeChunk.document_id == ingested.document_id
                )
            )
            == 1
        )
