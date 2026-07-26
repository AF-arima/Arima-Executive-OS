from dataclasses import dataclass
from email.message import EmailMessage
from typing import Protocol

from app.database.models import MailboxProvider


@dataclass(frozen=True, slots=True)
class OutboundAttachment:
    filename: str
    content_type: str
    size_bytes: int
    storage_key: str
    checksum_sha256: str


@dataclass(frozen=True, slots=True)
class OutboundMessage:
    from_email: str
    to_email: str
    cc: tuple[str, ...]
    bcc: tuple[str, ...]
    subject: str
    body_html: str
    body_text: str | None
    attachments: tuple[OutboundAttachment, ...]
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class ProviderResult:
    message_id: str


class HTTPMailboxTransport(Protocol):
    async def post_message(
        self,
        *,
        endpoint: str,
        credential_reference: str,
        payload: dict[str, object],
        idempotency_key: str,
    ) -> str: ...


class SMTPTransport(Protocol):
    async def send(
        self,
        *,
        credential_reference: str,
        message: EmailMessage,
        attachments: tuple[OutboundAttachment, ...],
        idempotency_key: str,
    ) -> str: ...


class MailboxAdapter(Protocol):
    async def send(
        self, message: OutboundMessage, credential_reference: str
    ) -> ProviderResult: ...


class GmailAdapter:
    def __init__(self, transport: HTTPMailboxTransport) -> None:
        self.transport = transport

    async def send(
        self, message: OutboundMessage, credential_reference: str
    ) -> ProviderResult:
        message_id = await self.transport.post_message(
            endpoint="https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
            credential_reference=credential_reference,
            payload=_payload(message),
            idempotency_key=message.idempotency_key,
        )
        return ProviderResult(message_id)


class Microsoft365Adapter:
    def __init__(self, transport: HTTPMailboxTransport) -> None:
        self.transport = transport

    async def send(
        self, message: OutboundMessage, credential_reference: str
    ) -> ProviderResult:
        message_id = await self.transport.post_message(
            endpoint="https://graph.microsoft.com/v1.0/me/sendMail",
            credential_reference=credential_reference,
            payload=_payload(message),
            idempotency_key=message.idempotency_key,
        )
        return ProviderResult(message_id)


class SMTPAdapter:
    def __init__(self, transport: SMTPTransport) -> None:
        self.transport = transport

    async def send(
        self, message: OutboundMessage, credential_reference: str
    ) -> ProviderResult:
        email = EmailMessage()
        email["From"] = message.from_email
        email["To"] = message.to_email
        if message.cc:
            email["Cc"] = ", ".join(message.cc)
        email["Subject"] = message.subject
        email.set_content(message.body_text or "This message requires HTML.")
        email.add_alternative(message.body_html, subtype="html")
        message_id = await self.transport.send(
            credential_reference=credential_reference,
            message=email,
            attachments=message.attachments,
            idempotency_key=message.idempotency_key,
        )
        return ProviderResult(message_id)


def adapter_for(
    provider: MailboxProvider,
    *,
    http_transport: HTTPMailboxTransport | None = None,
    smtp_transport: SMTPTransport | None = None,
) -> MailboxAdapter:
    if provider is MailboxProvider.GMAIL and http_transport is not None:
        return GmailAdapter(http_transport)
    if provider is MailboxProvider.MICROSOFT_365 and http_transport is not None:
        return Microsoft365Adapter(http_transport)
    if provider is MailboxProvider.SMTP and smtp_transport is not None:
        return SMTPAdapter(smtp_transport)
    raise ValueError("The selected mailbox provider transport is not configured")


def _payload(message: OutboundMessage) -> dict[str, object]:
    return {
        "from": message.from_email,
        "to": [message.to_email],
        "cc": list(message.cc),
        "bcc": list(message.bcc),
        "subject": message.subject,
        "body_html": message.body_html,
        "body_text": message.body_text,
        "attachments": [
            {
                "filename": item.filename,
                "content_type": item.content_type,
                "size_bytes": item.size_bytes,
                "storage_key": item.storage_key,
                "checksum_sha256": item.checksum_sha256,
            }
            for item in message.attachments
        ],
    }
