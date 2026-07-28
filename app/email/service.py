from app.auth.exceptions import EmailDeliveryError
from app.core.config import Settings, get_settings
from app.email.base import TransactionalEmailProvider
from app.email.exceptions import EmailProviderError
from app.email.templates import (
    email_change_email,
    link,
    login_notification_email,
    password_reset_email,
    security_alert_email,
    verification_email,
    welcome_email,
)
from app.email.types import EmailMessage


class TransactionalEmailService:
    def __init__(
        self,
        provider: TransactionalEmailProvider,
        settings: Settings | None = None,
    ) -> None:
        self.provider = provider
        self.settings = settings or get_settings()

    async def send_verification(
        self,
        *,
        email: str,
        recipient_name: str,
        token: str,
    ) -> None:
        template = verification_email(
            recipient_name=recipient_name,
            verification_url=link(self.settings.frontend_url, "/verify-email", token),
        )
        await self._send(email, template.subject, template.text_body, template.html_body)

    async def send_password_reset(
        self,
        *,
        email: str,
        recipient_name: str,
        token: str,
    ) -> None:
        template = password_reset_email(
            recipient_name=recipient_name,
            reset_url=link(self.settings.frontend_url, "/reset-password", token),
        )
        await self._send(email, template.subject, template.text_body, template.html_body)

    async def send_email_change(
        self,
        *,
        email: str,
        recipient_name: str,
        token: str,
    ) -> None:
        template = email_change_email(
            recipient_name=recipient_name,
            change_url=link(
                self.settings.frontend_url,
                "/verify-email",
                token,
                parameters={"purpose": "email_change"},
            ),
        )
        await self._send(email, template.subject, template.text_body, template.html_body)

    async def send_welcome(self, *, email: str, recipient_name: str) -> None:
        template = welcome_email(recipient_name=recipient_name)
        await self._send(email, template.subject, template.text_body, template.html_body)

    async def send_login_notification(
        self,
        *,
        email: str,
        recipient_name: str,
        ip_address: str | None,
        user_agent: str | None,
    ) -> None:
        template = login_notification_email(
            recipient_name=recipient_name,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        await self._send(email, template.subject, template.text_body, template.html_body)

    async def send_security_alert(
        self,
        *,
        email: str,
        recipient_name: str,
        event: str,
    ) -> None:
        template = security_alert_email(recipient_name=recipient_name, event=event)
        await self._send(email, template.subject, template.text_body, template.html_body)

    async def _send(
        self,
        to_address: str,
        subject: str,
        text_body: str,
        html_body: str,
    ) -> None:
        try:
            await self.provider.send(
                EmailMessage(
                    to_address=to_address,
                    subject=subject,
                    text_body=text_body,
                    html_body=html_body,
                )
            )
        except EmailProviderError as error:
            raise EmailDeliveryError from error
