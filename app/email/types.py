from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EmailMessage:
    to_address: str
    subject: str
    text_body: str
    html_body: str


@dataclass(frozen=True, slots=True)
class EmailDelivery:
    provider: str
    message_id: str | None = None
