import smtplib
from email.message import EmailMessage as MimeEmailMessage
from email.utils import make_msgid

from starlette.concurrency import run_in_threadpool

from app.core.config import Settings
from app.email.base import TransactionalEmailProvider
from app.email.exceptions import EmailProviderError
from app.email.types import EmailDelivery, EmailMessage


class SmtpEmailProvider(TransactionalEmailProvider):
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def provider_name(self) -> str:
        return "smtp"

    async def send(self, message: EmailMessage) -> EmailDelivery:
        message_id = await run_in_threadpool(self._send_sync, message)
        return EmailDelivery(provider=self.provider_name, message_id=message_id)

    def _send_sync(self, message: EmailMessage) -> str:
        from_address = self.settings.email_from_address
        smtp_host = self.settings.smtp_host
        smtp_username = self.settings.smtp_username
        smtp_password = self.settings.smtp_password
        if not all(
            (
                from_address,
                smtp_host,
                smtp_username,
                smtp_password,
            )
        ):
            raise EmailProviderError("SMTP email settings are incomplete")
        assert from_address is not None
        assert smtp_host is not None
        assert smtp_username is not None
        assert smtp_password is not None
        mime = MimeEmailMessage()
        mime["From"] = (
            f"{self.settings.email_from_name} "
            f"<{from_address}>"
        )
        mime["To"] = message.to_address
        mime["Subject"] = message.subject
        message_id = make_msgid(domain=from_address.split("@")[-1])
        mime["Message-ID"] = message_id
        mime.set_content(message.text_body)
        mime.add_alternative(message.html_body, subtype="html")

        try:
            if self.settings.smtp_use_ssl:
                client: smtplib.SMTP | smtplib.SMTP_SSL = smtplib.SMTP_SSL(
                    smtp_host,
                    self.settings.smtp_port,
                    timeout=15,
                )
            else:
                client = smtplib.SMTP(
                    smtp_host,
                    self.settings.smtp_port,
                    timeout=15,
                )
            with client:
                client.ehlo()
                if self.settings.smtp_use_tls and not self.settings.smtp_use_ssl:
                    client.starttls()
                    client.ehlo()
                client.login(
                    smtp_username,
                    smtp_password.get_secret_value(),
                )
                client.send_message(mime)
        except (OSError, smtplib.SMTPException) as error:
            raise EmailProviderError("SMTP delivery failed") from error
        return message_id
