from datetime import UTC, datetime
from uuid import uuid4

from pydantic import SecretStr
import pytest
from sqlalchemy import select
from fastapi.testclient import TestClient

from app.database.models import (
    AgentMessage,
    AgentRunStatus,
    MessageContentType,
    MessageRole,
    TelegramMessage,
    TelegramProcessingStatus,
)
from app.intelligence.access import (
    AgentGrantService,
    IntelligenceAccessError,
    RunBindingService,
)
from app.main import app
from app.telegram.schemas import GovernedWorkflowResult, TelegramEnvelope
from app.telegram.service import (
    TelegramAuthenticationError,
    TelegramIdentityService,
    TelegramTransportService,
    TelegramWebhookAuthenticator,
)
from tests.database.helpers import sqlite_session
from tests.intelligence.helpers import make_intelligence_context


def envelope(update_id: int = 1) -> TelegramEnvelope:
    return TelegramEnvelope(
        update_id=update_id,
        chat_id="chat-1",
        telegram_user_id="telegram-user-1",
        message_id=f"message-{update_id}",
        text="Give me today's executive briefing.",
        received_at=datetime.now(UTC),
    )


def test_telegram_webhook_is_documented_and_fails_closed_by_default() -> None:
    routes = {
        path: frozenset(methods)
        for path, methods in app.openapi()["paths"].items()
        if path.startswith("/api/v1/telegram")
    }
    assert routes == {"/api/v1/telegram/webhook": frozenset({"post"})}

    response = TestClient(app).post(
        "/api/v1/telegram/webhook",
        headers={"X-Telegram-Bot-Api-Secret-Token": "invalid"},
        json={
            "update_id": 100,
            "message": {
                "message_id": 200,
                "date": 1_786_665_600,
                "chat": {"id": 300, "type": "private", "first_name": "A"},
                "from": {"id": 400, "is_bot": False, "first_name": "A"},
                "text": "Give me today's executive briefing.",
                "entities": [{"type": "bot_command", "offset": 0, "length": 4}],
            },
            "unexpected_future_field": {"safe": True},
        },
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "Telegram webhook authentication failed"}


@pytest.mark.asyncio
async def test_telegram_requires_webhook_and_verified_arima_identity() -> None:
    async with sqlite_session() as session:
        called = 0

        async def workflow(*_: object) -> GovernedWorkflowResult:
            nonlocal called
            called += 1
            return GovernedWorkflowResult(
                conversation_id=uuid4(), run_id=uuid4(), response="Briefing"
            )

        service = TelegramTransportService(
            session,
            TelegramWebhookAuthenticator(
                enabled=True, secret=SecretStr("webhook-secret")
            ),
            workflow,
        )
        with pytest.raises(TelegramAuthenticationError):
            await service.process(envelope(), supplied_secret="wrong")
        assert await session.scalar(select(TelegramMessage)) is None

        with pytest.raises(TelegramAuthenticationError):
            await service.process(envelope(), supplied_secret="webhook-secret")
        persisted = await session.scalar(select(TelegramMessage))
        assert persisted is not None
        assert persisted.status is TelegramProcessingStatus.FAILED
        assert persisted.error_code == "unverified_telegram_identity"
        assert called == 0


@pytest.mark.asyncio
async def test_telegram_mapping_replay_and_cross_tenant_protection() -> None:
    async with sqlite_session() as session:
        context = await make_intelligence_context(session)
        attacker = await make_intelligence_context(session)
        identity_service = TelegramIdentityService(session)
        await identity_service.verify(
            workspace_id=context.workspace.id,
            actor=context.user,
            telegram_user_id="telegram-user-1",
            telegram_chat_id="chat-1",
        )
        with pytest.raises(IntelligenceAccessError):
            await identity_service.verify(
                workspace_id=attacker.workspace.id,
                actor=attacker.user,
                telegram_user_id="telegram-user-1",
                telegram_chat_id="chat-1",
            )
        await AgentGrantService(session).grant(
            workspace_id=context.workspace.id,
            agent_id=context.agent.id,
            actor=context.user,
        )
        await RunBindingService(session).bind(
            workspace_id=context.workspace.id,
            run=context.run,
            actor=context.user,
            channel="telegram-test",
        )
        called = 0

        async def workflow(*_: object) -> GovernedWorkflowResult:
            nonlocal called
            called += 1
            return GovernedWorkflowResult(
                conversation_id=context.conversation.id,
                run_id=context.run.id,
                response="Governed briefing",
            )

        service = TelegramTransportService(
            session,
            TelegramWebhookAuthenticator(
                enabled=True, secret=SecretStr("webhook-secret")
            ),
            workflow,
        )
        wrong_chat = envelope(9).model_copy(update={"chat_id": "wrong-chat"})
        with pytest.raises(TelegramAuthenticationError):
            await service.process(wrong_chat, supplied_secret="webhook-secret")
        assert called == 0

        output = AgentMessage(
            conversation_id=context.conversation.id,
            run_id=context.run.id,
            role=MessageRole.ASSISTANT,
            content="Governed briefing",
            content_type=MessageContentType.MARKDOWN,
            sequence_number=1,
            metadata_={},
            created_by_id=None,
        )
        session.add(output)
        await session.flush()
        context.run.status = AgentRunStatus.COMPLETED
        context.run.output_message_id = output.id
        await session.commit()
        first = await service.process(envelope(10), supplied_secret="webhook-secret")
        replay = await service.process(envelope(10), supplied_secret="webhook-secret")

        assert first.id == replay.id
        assert called == 1
        assert first.workspace_id == context.workspace.id
        assert first.user_id == context.user.id
        assert first.incoming_text
        assert first.outgoing_text == "Governed briefing"
        assert first.status is TelegramProcessingStatus.COMPLETED
        assert first.processing_started_at is not None
        assert first.completed_at is not None
        delivered = await service.mark_delivered(
            message_id=first.id,
            outgoing_message_id="telegram-outgoing-10",
        )
        assert delivered.outgoing_message_id == "telegram-outgoing-10"

        mismatched_replay = envelope(10).model_copy(
            update={
                "chat_id": "attacker-chat",
                "telegram_user_id": "attacker-user",
            }
        )
        with pytest.raises(TelegramAuthenticationError):
            await service.process(mismatched_replay, supplied_secret="webhook-secret")
        assert called == 1

        async def cross_tenant_workflow(*_: object) -> GovernedWorkflowResult:
            return GovernedWorkflowResult(
                conversation_id=attacker.conversation.id,
                run_id=attacker.run.id,
                response="Must not cross tenants",
            )

        cross_tenant = TelegramTransportService(
            session,
            TelegramWebhookAuthenticator(
                enabled=True, secret=SecretStr("webhook-secret")
            ),
            cross_tenant_workflow,
        )
        with pytest.raises(IntelligenceAccessError):
            await cross_tenant.process(envelope(11), supplied_secret="webhook-secret")
        failed = await session.scalar(
            select(TelegramMessage).where(TelegramMessage.update_id == 11)
        )
        assert failed is not None
        assert failed.status is TelegramProcessingStatus.FAILED
        assert failed.conversation_id is None
        assert failed.run_id is None


@pytest.mark.asyncio
async def test_telegram_persists_processing_errors() -> None:
    async with sqlite_session() as session:
        context = await make_intelligence_context(session)
        await TelegramIdentityService(session).verify(
            workspace_id=context.workspace.id,
            actor=context.user,
            telegram_user_id="telegram-user-1",
            telegram_chat_id="chat-1",
        )

        async def workflow(*_: object) -> GovernedWorkflowResult:
            raise RuntimeError("provider unavailable")

        service = TelegramTransportService(
            session,
            TelegramWebhookAuthenticator(
                enabled=True, secret=SecretStr("webhook-secret")
            ),
            workflow,
        )
        with pytest.raises(RuntimeError, match="provider unavailable"):
            await service.process(envelope(20), supplied_secret="webhook-secret")
        message = await session.scalar(
            select(TelegramMessage).where(TelegramMessage.update_id == 20)
        )
        assert message is not None
        assert message.status is TelegramProcessingStatus.FAILED
        assert message.error_code == "telegram_processing_failed"
        assert message.error_message == "Telegram processing failed (RuntimeError)"
