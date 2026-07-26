import asyncio
from email.message import EmailMessage

import pytest
from pydantic import ValidationError

from app.database.models import MailboxProvider
from app.schemas.outreach import MailboxCreate
from app.services.mailbox_providers import (
    GmailAdapter,
    OutboundMessage,
    SMTPAdapter,
)


class HTTPTransport:
    def __init__(self) -> None:
        self.endpoint = ""
        self.reference = ""
        self.payload: dict[str, object] = {}

    async def post_message(
        self,
        *,
        endpoint: str,
        credential_reference: str,
        payload: dict[str, object],
        idempotency_key: str,
    ) -> str:
        self.endpoint = endpoint
        self.reference = credential_reference
        self.payload = payload
        assert idempotency_key == "queue-1"
        return "gmail-message-1"


class SMTPTransportStub:
    def __init__(self) -> None:
        self.message: EmailMessage | None = None

    async def send(
        self,
        *,
        credential_reference: str,
        message: EmailMessage,
        attachments: tuple[object, ...],
        idempotency_key: str,
    ) -> str:
        assert credential_reference == "vault://smtp/test"
        assert idempotency_key == "queue-1"
        assert attachments == ()
        self.message = message
        return "smtp-message-1"


def message() -> OutboundMessage:
    return OutboundMessage(
        from_email="sender@example.com",
        to_email="target@example.com",
        cc=("copy@example.com",),
        bcc=(),
        subject="Hello",
        body_html="<p>Hello</p>",
        body_text="Hello",
        attachments=(),
        idempotency_key="queue-1",
    )


def test_mailbox_schema_rejects_embedded_secrets() -> None:
    with pytest.raises(ValidationError):
        MailboxCreate(
            provider=MailboxProvider.GMAIL,
            email_address="sender@example.com",
            credential_reference="token=raw-secret",
        )


def test_gmail_and_smtp_adapters_delegate_without_storing_credentials() -> None:
    async def exercise() -> None:
        http = HTTPTransport()
        gmail_result = await GmailAdapter(http).send(message(), "vault://gmail/test")
        assert gmail_result.message_id == "gmail-message-1"
        assert http.reference == "vault://gmail/test"
        assert http.payload["subject"] == "Hello"
        assert "gmail.googleapis.com" in http.endpoint

        smtp = SMTPTransportStub()
        smtp_result = await SMTPAdapter(smtp).send(message(), "vault://smtp/test")
        assert smtp_result.message_id == "smtp-message-1"
        assert smtp.message is not None
        assert smtp.message["To"] == "target@example.com"
        assert smtp.message["Cc"] == "copy@example.com"

    asyncio.run(exercise())
