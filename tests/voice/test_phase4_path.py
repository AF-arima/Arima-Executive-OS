from datetime import UTC, datetime
from typing import cast

import pytest
from sqlalchemy import select

from app.database.models import (
    AIWorkspaceRun,
    AgentMessage,
    AgentRun,
    AgentRunStatus,
    MessageRole,
)
from app.intelligence.access import AgentGrantService
from app.intelligence.audit import AuditChainService
from app.intelligence.ingestion import KnowledgeIngestionService
from app.intelligence.schemas import (
    KnowledgeDocumentInput,
    KnowledgeSourceInput,
)
from app.orchestration.engine import OrchestrationEngine
from app.services.exceptions import PermissionDeniedError
from app.voice.exceptions import VoicePermissionDenied
from app.voice.factory import (
    VoiceGatewayFactory,
    VoiceOrchestrationContextFactory,
)
from app.voice.orchestration import DurableVoiceOrchestration
from app.voice.schemas import VoiceSessionCreate
from app.voice.session import VoiceSessionStore
from tests.database.helpers import sqlite_session
from tests.intelligence.helpers import make_intelligence_context


class FailingEngine:
    async def execute(self, _: object) -> None:
        raise RuntimeError("private-provider-detail")

    async def health(self) -> list[object]:
        return []


@pytest.mark.asyncio
async def test_voice_uses_existing_authorized_phase4_audit_chain() -> None:
    async with sqlite_session() as database:
        seed = await make_intelligence_context(database)
        await AgentGrantService(database).grant(
            workspace_id=seed.workspace.id,
            agent_id=seed.agent.id,
            actor=seed.user,
        )
        ingestion = KnowledgeIngestionService(database)
        source = await ingestion.create_source(
            workspace_id=seed.workspace.id,
            actor=seed.user,
            data=KnowledgeSourceInput(
                source_type="workspace",
                external_id="voice-source",
                name="Voice source",
                freshness_required=True,
                max_age_seconds=3_600,
            ),
        )
        await ingestion.ingest(
            workspace_id=seed.workspace.id,
            source_id=source.id,
            actor=seed.user,
            data=KnowledgeDocumentInput(
                external_id="voice-document",
                title="Voice document",
                content="Strategic decision evidence is approved.",
                source_observed_at=datetime.now(UTC),
                provenance={"source": "voice-source"},
            ),
        )
        sessions = VoiceSessionStore(database)
        voice_session, _ = await VoiceGatewayFactory(
            database, sessions=sessions
        ).create().create_session(
            VoiceSessionCreate(conversation_id=seed.conversation.id),
            seed.user,
        )

        response = await VoiceGatewayFactory(
            database, sessions=sessions
        ).create().handle_transcript(
            voice_session.session_id,
            "Analyse strategic decision evidence",
            seed.user,
        )
        persisted_voice = await sessions.get(
            voice_session.session_id, seed.user.id
        )
        run = await database.get(AgentRun, persisted_voice.run_id)
        output = await database.scalar(
            select(AgentMessage).where(
                AgentMessage.run_id == persisted_voice.run_id,
                AgentMessage.role == MessageRole.ASSISTANT,
            )
        )
        chain = await AuditChainService(database).for_run(
            workspace_id=seed.workspace.id,
            run_id=persisted_voice.run_id,
            actor=seed.user,
        )

        assert response.response_text.startswith("Mock response:")
        assert persisted_voice.conversation_id == seed.conversation.id
        assert run is not None
        assert run.status is AgentRunStatus.COMPLETED
        assert run.model_provider == "mock"
        assert output is not None
        assert output.id == run.output_message_id
        assert output.created_by_id is None
        assert chain.workspace_id == seed.workspace.id
        assert chain.retrieved_context_ids
        assert chain.output_message_id == output.id


@pytest.mark.asyncio
async def test_voice_session_transcripts_receive_distinct_run_correlations() -> None:
    async with sqlite_session() as database:
        seed = await make_intelligence_context(database)
        await AgentGrantService(database).grant(
            workspace_id=seed.workspace.id,
            agent_id=seed.agent.id,
            actor=seed.user,
        )
        sessions = VoiceSessionStore(database)
        gateway = VoiceGatewayFactory(database, sessions=sessions).create()
        voice_session, _ = await gateway.create_session(
            VoiceSessionCreate(conversation_id=seed.conversation.id),
            seed.user,
        )

        first = await gateway.handle_transcript(
            voice_session.session_id,
            "Analyse the first strategic decision",
            seed.user,
        )
        second = await gateway.handle_transcript(
            voice_session.session_id,
            "Analyse the second strategic decision",
            seed.user,
        )
        bindings = (
            await database.scalars(
                select(AIWorkspaceRun).where(
                    AIWorkspaceRun.workspace_id == seed.workspace.id,
                    AIWorkspaceRun.user_id == seed.user.id,
                    AIWorkspaceRun.channel == "voice",
                )
            )
        ).all()
        persisted_voice = await sessions.get(
            voice_session.session_id, seed.user.id
        )

        assert len(bindings) == 2
        assert len({binding.correlation_id for binding in bindings}) == 2
        assert first.correlation_id != second.correlation_id
        assert persisted_voice.correlation_id == second.correlation_id


@pytest.mark.asyncio
async def test_voice_rejects_missing_grant_before_ai_run() -> None:
    async with sqlite_session() as database:
        seed = await make_intelligence_context(database)
        sessions = VoiceSessionStore(database)
        voice_session = await sessions.create(
            VoiceSessionCreate(conversation_id=seed.conversation.id),
            seed.user.id,
        )

        with pytest.raises(VoicePermissionDenied):
            await VoiceOrchestrationContextFactory(database)(
                voice_session,
                seed.user,
                "Analyse this decision",
            )

        runs = (
            await database.scalars(
                select(AgentRun).where(
                    AgentRun.conversation_id == seed.conversation.id
                )
            )
        ).all()
        assert runs == [seed.run]


@pytest.mark.asyncio
async def test_voice_rejects_legacy_conversation_without_workspace_metadata() -> None:
    async with sqlite_session() as database:
        seed = await make_intelligence_context(database)
        seed.conversation.metadata_ = {}
        await database.commit()
        sessions = VoiceSessionStore(database)
        voice_session = await sessions.create(
            VoiceSessionCreate(conversation_id=seed.conversation.id),
            seed.user.id,
        )

        with pytest.raises(VoicePermissionDenied):
            await VoiceOrchestrationContextFactory(database)(
                voice_session,
                seed.user,
                "Analyse this decision",
            )


@pytest.mark.asyncio
async def test_voice_rejects_malformed_workspace_metadata_without_repair() -> None:
    async with sqlite_session() as database:
        seed = await make_intelligence_context(database)
        seed.conversation.metadata_ = {"workspace_id": "not-a-workspace-id"}
        await database.commit()
        sessions = VoiceSessionStore(database)
        voice_session = await sessions.create(
            VoiceSessionCreate(conversation_id=seed.conversation.id),
            seed.user.id,
        )

        with pytest.raises(VoicePermissionDenied):
            await VoiceOrchestrationContextFactory(database)(
                voice_session,
                seed.user,
                "Analyse this decision",
            )

        conversation = await database.get(
            type(seed.conversation), seed.conversation.id
        )
        assert conversation is not None
        assert conversation.metadata_["workspace_id"] == "not-a-workspace-id"


@pytest.mark.asyncio
async def test_voice_rejects_non_invoking_role_after_grant() -> None:
    async with sqlite_session() as database:
        seed = await make_intelligence_context(database, role_name="viewer")
        await AgentGrantService(database).grant(
            workspace_id=seed.workspace.id,
            agent_id=seed.agent.id,
            actor=seed.user,
        )
        voice_session = await VoiceSessionStore(database).create(
            VoiceSessionCreate(conversation_id=seed.conversation.id),
            seed.user.id,
        )

        with pytest.raises(PermissionDeniedError):
            await VoiceOrchestrationContextFactory(database)(
                voice_session,
                seed.user,
                "Analyse this decision",
            )

        runs = (
            await database.scalars(
                select(AgentRun).where(
                    AgentRun.conversation_id == seed.conversation.id
                )
            )
        ).all()
        assert runs == [seed.run]


@pytest.mark.asyncio
async def test_voice_rejects_cross_tenant_conversation_before_ai_run() -> None:
    async with sqlite_session() as database:
        owner = await make_intelligence_context(database)
        attacker = await make_intelligence_context(database)
        await AgentGrantService(database).grant(
            workspace_id=owner.workspace.id,
            agent_id=owner.agent.id,
            actor=owner.user,
        )
        voice_session = await VoiceSessionStore(database).create(
            VoiceSessionCreate(conversation_id=owner.conversation.id),
            attacker.user.id,
        )

        with pytest.raises(VoicePermissionDenied):
            await VoiceOrchestrationContextFactory(database)(
                voice_session,
                attacker.user,
                "Analyse the owner's decision",
            )

        owner_runs = (
            await database.scalars(
                select(AgentRun).where(
                    AgentRun.conversation_id == owner.conversation.id
                )
            )
        ).all()
        assert owner_runs == [owner.run]


@pytest.mark.asyncio
async def test_voice_provider_failure_marks_run_failed_without_detail() -> None:
    async with sqlite_session() as database:
        seed = await make_intelligence_context(database)
        await AgentGrantService(database).grant(
            workspace_id=seed.workspace.id,
            agent_id=seed.agent.id,
            actor=seed.user,
        )
        voice_session = await VoiceSessionStore(database).create(
            VoiceSessionCreate(conversation_id=seed.conversation.id),
            seed.user.id,
        )
        context = await VoiceOrchestrationContextFactory(database)(
            voice_session,
            seed.user,
            "Analyse this decision",
        )
        durable = DurableVoiceOrchestration(
            database,
            cast(OrchestrationEngine, FailingEngine()),
        )

        with pytest.raises(RuntimeError, match="private-provider-detail"):
            await durable.execute(context)

        run = await database.get(AgentRun, context.run.id)
        assert run is not None
        assert run.status is AgentRunStatus.FAILED
        assert run.failure_code == "voice_ai_workflow_failed"
        assert run.failure_message == "Voice AI workflow failed (RuntimeError)"
        assert "private-provider-detail" not in run.failure_message
