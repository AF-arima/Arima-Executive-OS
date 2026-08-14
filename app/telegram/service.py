from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
import hmac
from uuid import UUID

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    AIWorkspaceRun,
    AgentConversation,
    AgentMessage,
    AgentRun,
    AgentRunStatus,
    TelegramIdentity,
    TelegramIdentityStatus,
    TelegramMessage,
    TelegramProcessingStatus,
    User,
)
from app.intelligence.access import (
    IntelligenceAccessError,
    require_workspace_membership,
)
from app.services.permissions import can_invoke_agents
from app.telegram.schemas import GovernedWorkflowResult, TelegramEnvelope


class TelegramAuthenticationError(PermissionError):
    pass


WorkflowHandler = Callable[[UUID, User, str, UUID], Awaitable[GovernedWorkflowResult]]


def _now() -> datetime:
    return datetime.now(UTC)


def _safe_failure_detail(error: Exception) -> str:
    return f"Telegram processing failed ({type(error).__name__})"


class TelegramWebhookAuthenticator:
    def __init__(self, *, enabled: bool, secret: SecretStr | None) -> None:
        self.enabled = enabled
        self.secret = secret

    def authenticate(self, supplied_secret: str | None) -> None:
        if not self.enabled or self.secret is None or supplied_secret is None:
            raise TelegramAuthenticationError("Telegram transport is unavailable")
        expected = self.secret.get_secret_value()
        if not expected or not hmac.compare_digest(expected, supplied_secret):
            raise TelegramAuthenticationError("Telegram webhook is unauthorized")


class TelegramIdentityService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def verify(
        self,
        *,
        workspace_id: UUID,
        actor: User,
        telegram_user_id: str,
        telegram_chat_id: str,
    ) -> TelegramIdentity:
        await require_workspace_membership(self.session, actor, workspace_id)
        identity = await self.session.scalar(
            select(TelegramIdentity).where(
                TelegramIdentity.telegram_user_id == telegram_user_id
            )
        )
        if identity is not None and (
            identity.workspace_id != workspace_id or identity.user_id != actor.id
        ):
            raise IntelligenceAccessError(
                "Telegram identity is already bound to another Arima user"
            )
        if identity is None:
            identity = TelegramIdentity(
                workspace_id=workspace_id,
                user_id=actor.id,
                telegram_user_id=telegram_user_id,
                telegram_chat_id=telegram_chat_id,
                status=TelegramIdentityStatus.VERIFIED,
                verified_at=_now(),
                verified_by_id=actor.id,
            )
            self.session.add(identity)
        else:
            identity.telegram_chat_id = telegram_chat_id
            identity.status = TelegramIdentityStatus.VERIFIED
            identity.verified_at = _now()
            identity.verified_by_id = actor.id
            identity.revoked_at = None
        await self.session.commit()
        return identity

    async def revoke(self, *, identity_id: UUID, actor: User) -> None:
        identity = await self.session.get(TelegramIdentity, identity_id)
        if identity is None or identity.user_id != actor.id:
            raise IntelligenceAccessError("Telegram identity is unavailable")
        await require_workspace_membership(self.session, actor, identity.workspace_id)
        identity.status = TelegramIdentityStatus.REVOKED
        identity.revoked_at = _now()
        await self.session.commit()


class TelegramTransportService:
    """Persists every accepted update and invokes an authorized workflow."""

    def __init__(
        self,
        session: AsyncSession,
        authenticator: TelegramWebhookAuthenticator,
        workflow: WorkflowHandler,
    ) -> None:
        self.session = session
        self.authenticator = authenticator
        self.workflow = workflow

    async def process(
        self,
        envelope: TelegramEnvelope,
        *,
        supplied_secret: str | None,
    ) -> TelegramMessage:
        self.authenticator.authenticate(supplied_secret)
        existing = await self.session.scalar(
            select(TelegramMessage).where(
                TelegramMessage.update_id == envelope.update_id
            )
        )
        if existing is not None:
            self._require_matching_replay(existing, envelope)
            return existing

        message = TelegramMessage(
            update_id=envelope.update_id,
            chat_id=envelope.chat_id,
            telegram_user_id=envelope.telegram_user_id,
            incoming_message_id=envelope.message_id,
            incoming_text=envelope.text,
            status=TelegramProcessingStatus.RECEIVED,
            received_at=envelope.received_at.astimezone(UTC),
        )
        self.session.add(message)
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            replay = await self.session.scalar(
                select(TelegramMessage).where(
                    TelegramMessage.update_id == envelope.update_id
                )
            )
            if replay is None:
                raise
            self._require_matching_replay(replay, envelope)
            return replay

        identity = await self.session.scalar(
            select(TelegramIdentity).where(
                TelegramIdentity.telegram_user_id == envelope.telegram_user_id,
                TelegramIdentity.telegram_chat_id == envelope.chat_id,
                TelegramIdentity.status == TelegramIdentityStatus.VERIFIED,
                TelegramIdentity.revoked_at.is_(None),
            )
        )
        if identity is None:
            await self._fail(
                message,
                code="unverified_telegram_identity",
                detail="Telegram identity is not verified for Arima",
            )
            raise TelegramAuthenticationError("A verified Arima identity is required")

        user = await self.session.get(User, identity.user_id)
        try:
            if user is None:
                raise IntelligenceAccessError("Arima user is unavailable")
            await require_workspace_membership(
                self.session, user, identity.workspace_id
            )
            if not can_invoke_agents(user):
                raise IntelligenceAccessError(
                    "Arima agent invocation is not authorized"
                )
            message.workspace_id = identity.workspace_id
            message.user_id = user.id
            message.identity_id = identity.id
            message.status = TelegramProcessingStatus.PROCESSING
            message.processing_started_at = _now()
            await self.session.commit()
            result = await self.workflow(
                identity.workspace_id,
                user,
                envelope.text,
                message.id,
            )
            await self._require_owned_result(
                result=result,
                identity=identity,
                user=user,
            )
        except Exception as error:
            await self._fail(
                message,
                code="telegram_processing_failed",
                detail=_safe_failure_detail(error),
            )
            raise

        message.conversation_id = result.conversation_id
        message.run_id = result.run_id
        message.outgoing_text = result.response
        message.status = TelegramProcessingStatus.COMPLETED
        message.completed_at = _now()
        message.error_code = None
        message.error_message = None
        await self.session.commit()
        return message

    @staticmethod
    def _require_matching_replay(
        message: TelegramMessage,
        envelope: TelegramEnvelope,
    ) -> None:
        if (
            message.chat_id != envelope.chat_id
            or message.telegram_user_id != envelope.telegram_user_id
            or message.incoming_message_id != envelope.message_id
            or message.incoming_text != envelope.text
        ):
            raise TelegramAuthenticationError(
                "Telegram replay payload does not match the original update"
            )

    async def _require_owned_result(
        self,
        *,
        result: GovernedWorkflowResult,
        identity: TelegramIdentity,
        user: User,
    ) -> None:
        binding = await self.session.scalar(
            select(AIWorkspaceRun).where(
                AIWorkspaceRun.workspace_id == identity.workspace_id,
                AIWorkspaceRun.user_id == user.id,
                AIWorkspaceRun.run_id == result.run_id,
            )
        )
        run = await self.session.get(AgentRun, result.run_id)
        conversation = await self.session.get(AgentConversation, result.conversation_id)
        output = (
            await self.session.get(AgentMessage, run.output_message_id)
            if run is not None and run.output_message_id is not None
            else None
        )
        if (
            binding is None
            or run is None
            or conversation is None
            or output is None
            or run.status is not AgentRunStatus.COMPLETED
            or run.conversation_id != conversation.id
            or run.triggered_by_id != user.id
            or conversation.owner_id != user.id
            or output.run_id != run.id
            or output.conversation_id != conversation.id
            or output.content != result.response
        ):
            raise IntelligenceAccessError(
                "Telegram workflow result ownership is invalid"
            )

    async def mark_delivered(
        self,
        *,
        message_id: UUID,
        outgoing_message_id: str,
    ) -> TelegramMessage:
        message = await self.session.get(TelegramMessage, message_id)
        if message is None or message.status is not TelegramProcessingStatus.COMPLETED:
            raise ValueError("Completed Telegram message is required")
        message.outgoing_message_id = outgoing_message_id
        await self.session.commit()
        return message

    async def _fail(
        self,
        message: TelegramMessage,
        *,
        code: str,
        detail: str,
    ) -> None:
        message.status = TelegramProcessingStatus.FAILED
        message.completed_at = _now()
        message.error_code = code
        message.error_message = detail[:2000]
        await self.session.commit()
