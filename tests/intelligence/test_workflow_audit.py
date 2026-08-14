from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import cast

import pytest
from sqlalchemy import select

from app.database.models import (
    AgentMessage,
    AgentRun,
    AgentRunStatus,
    WorkspaceMembership,
)
from app.intelligence.access import AgentGrantService, IntelligenceAccessError
from app.intelligence.audit import AuditChainService
from app.intelligence.ingestion import KnowledgeIngestionService
from app.intelligence.schemas import (
    KnowledgeDocumentInput,
    KnowledgeSourceInput,
)
from app.intelligence.workflow import ExecutiveWorkflowService
from app.orchestration.engine import OrchestrationEngine
from tests.database.helpers import sqlite_session
from tests.intelligence.helpers import make_intelligence_context


class SuccessfulEngine:
    async def execute(self, _: object) -> SimpleNamespace:
        return SimpleNamespace(
            final_response="## Briefing\n\nLaunch is approved [evidence].",
            route=SimpleNamespace(provider="mock", model="governed-test"),
            costs=SimpleNamespace(
                input_tokens=20,
                output_tokens=10,
                total_cost_gbp=Decimal("0"),
            ),
        )


class FailingEngine:
    async def execute(self, _: object) -> None:
        raise RuntimeError("orchestration unavailable")


@pytest.mark.asyncio
async def test_executive_workflow_records_full_audit_chain() -> None:
    async with sqlite_session() as session:
        context = await make_intelligence_context(session)
        await AgentGrantService(session).grant(
            workspace_id=context.workspace.id,
            agent_id=context.agent.id,
            actor=context.user,
        )
        ingestion = KnowledgeIngestionService(session)
        source = await ingestion.create_source(
            workspace_id=context.workspace.id,
            actor=context.user,
            data=KnowledgeSourceInput(
                source_type="workspace",
                external_id="briefing-source",
                name="Briefing source",
                freshness_required=True,
                max_age_seconds=3600,
            ),
        )
        await ingestion.ingest(
            workspace_id=context.workspace.id,
            source_id=source.id,
            actor=context.user,
            data=KnowledgeDocumentInput(
                external_id="briefing-document",
                title="Briefing document",
                content="Today's launch is approved by the executive team.",
                source_observed_at=datetime.now(UTC),
                provenance={"source": "briefing-source"},
            ),
        )
        service = ExecutiveWorkflowService(
            session, cast(OrchestrationEngine, SuccessfulEngine())
        )

        result = await service.execute_briefing(
            workspace_id=context.workspace.id,
            agent_id=context.agent.id,
            actor=context.user,
            request="Give me today's launch executive briefing.",
        )
        chain = await AuditChainService(session).for_run(
            workspace_id=context.workspace.id,
            run_id=result.run_id,
            actor=context.user,
        )
        output = await session.get(AgentMessage, result.output_message_id)

        assert chain.user_id == context.user.id
        assert chain.workspace_id == context.workspace.id
        assert chain.agent_id == context.agent.id
        assert chain.run_status == AgentRunStatus.COMPLETED.value
        assert chain.retrieved_context_ids == result.evidence_ids
        assert chain.output_message_id == result.output_message_id
        assert output is not None
        assert output.created_by_id is None
        assert output.run_id == result.run_id

        attacker = await make_intelligence_context(session)
        session.add(
            WorkspaceMembership(
                workspace_id=context.workspace.id,
                user_id=attacker.user.id,
                role="member",
            )
        )
        await session.commit()
        with pytest.raises(IntelligenceAccessError):
            await AuditChainService(session).for_run(
                workspace_id=context.workspace.id,
                run_id=result.run_id,
                actor=attacker.user,
            )


@pytest.mark.asyncio
async def test_failed_orchestration_transitions_durable_ai_run() -> None:
    async with sqlite_session() as session:
        context = await make_intelligence_context(session)
        await AgentGrantService(session).grant(
            workspace_id=context.workspace.id,
            agent_id=context.agent.id,
            actor=context.user,
        )
        service = ExecutiveWorkflowService(
            session, cast(OrchestrationEngine, FailingEngine())
        )

        with pytest.raises(RuntimeError, match="orchestration unavailable"):
            await service.execute_briefing(
                workspace_id=context.workspace.id,
                agent_id=context.agent.id,
                actor=context.user,
                request="Give me today's executive briefing.",
            )
        failed = await session.scalar(
            select(AgentRun)
            .where(AgentRun.id != context.run.id)
            .order_by(AgentRun.created_at.desc())
        )

        assert failed is not None
        assert failed.status is AgentRunStatus.FAILED
        assert failed.failure_code == "executive_workflow_failed"
        assert failed.failure_message == "Executive workflow failed (RuntimeError)"
