from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, status

from app.api.v1.dependencies import SessionDependency
from app.core.config import get_settings
from app.intelligence.access import IntelligenceAccessError
from app.intelligence.workflow import ExecutiveWorkflowService
from app.orchestration.factory import OrchestrationFactory
from app.services.agent import AgentService
from app.services.exceptions import ResourceNotFoundError
from app.telegram.schemas import (
    GovernedWorkflowResult,
    TelegramEnvelope,
    TelegramUpdate,
    TelegramWebhookReply,
)
from app.telegram.service import (
    TelegramAuthenticationError,
    TelegramTransportService,
    TelegramWebhookAuthenticator,
)

router = APIRouter(prefix="/telegram", tags=["telegram"])
TelegramSecret = Annotated[
    str | None,
    Header(alias="X-Telegram-Bot-Api-Secret-Token"),
]


@router.post(
    "/webhook",
    response_model=TelegramWebhookReply,
    responses={
        401: {"description": "Invalid Telegram webhook authentication"},
        403: {"description": "Telegram identity or Arima authorization denied"},
        503: {"description": "Governed workflow unavailable"},
    },
)
async def telegram_webhook(
    update: TelegramUpdate,
    database: SessionDependency,
    secret: TelegramSecret = None,
) -> TelegramWebhookReply:
    settings = get_settings()
    authenticator = TelegramWebhookAuthenticator(
        enabled=settings.telegram_enabled,
        secret=settings.telegram_webhook_secret,
    )
    try:
        authenticator.authenticate(secret)
    except TelegramAuthenticationError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Telegram webhook authentication failed",
        ) from error

    async def execute(
        workspace_id,
        actor,
        content,
        correlation_id,
    ) -> GovernedWorkflowResult:
        agent = await AgentService(database).get_default()
        result = await ExecutiveWorkflowService(
            database, OrchestrationFactory(database).create()
        ).execute_briefing(
            workspace_id=workspace_id,
            agent_id=agent.id,
            actor=actor,
            request=content,
            channel="telegram",
            correlation_id=correlation_id,
        )
        return GovernedWorkflowResult(
            conversation_id=result.conversation_id,
            run_id=result.run_id,
            response=result.response,
        )

    envelope = TelegramEnvelope(
        update_id=update.update_id,
        chat_id=str(update.message.chat.id),
        telegram_user_id=str(update.message.sender.id),
        message_id=str(update.message.message_id),
        text=update.message.text,
        received_at=datetime.fromtimestamp(update.message.date, tz=UTC),
    )
    try:
        processed = await TelegramTransportService(
            database, authenticator, execute
        ).process(envelope, supplied_secret=secret)
    except TelegramAuthenticationError as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Telegram authorization denied",
        ) from error
    except (IntelligenceAccessError, ResourceNotFoundError) as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Arima authorization denied",
        ) from error
    except Exception as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Governed Telegram workflow unavailable",
        ) from error
    if processed.outgoing_text is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Governed Telegram workflow produced no response",
        )
    return TelegramWebhookReply(
        chat_id=update.message.chat.id,
        text=processed.outgoing_text,
    )
