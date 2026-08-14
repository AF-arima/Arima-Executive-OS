from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TelegramSchema(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TelegramWebhookSchema(BaseModel):
    # Telegram adds fields to updates over time; consume only the authenticated
    # identity, chat, message, timestamp, and text fields used by this transport.
    model_config = ConfigDict(extra="ignore", frozen=True)


class TelegramEnvelope(TelegramSchema):
    update_id: int = Field(ge=0)
    chat_id: str = Field(min_length=1, max_length=100)
    telegram_user_id: str = Field(min_length=1, max_length=100)
    message_id: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=100_000)
    received_at: datetime

    @field_validator("received_at")
    @classmethod
    def require_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Telegram timestamp must be timezone-aware")
        return value


class GovernedWorkflowResult(TelegramSchema):
    conversation_id: UUID
    run_id: UUID
    response: str = Field(min_length=1, max_length=500_000)


class TelegramSender(TelegramWebhookSchema):
    id: int


class TelegramChat(TelegramWebhookSchema):
    id: int


class TelegramInboundMessage(TelegramWebhookSchema):
    message_id: int
    date: int = Field(ge=0)
    chat: TelegramChat
    sender: TelegramSender = Field(alias="from")
    text: str = Field(min_length=1, max_length=100_000)


class TelegramUpdate(TelegramWebhookSchema):
    update_id: int = Field(ge=0)
    message: TelegramInboundMessage


class TelegramWebhookReply(TelegramSchema):
    method: str = "sendMessage"
    chat_id: int
    text: str
