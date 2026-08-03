from collections.abc import Mapping
from email.utils import formataddr

import httpx

from app.core.config import Settings
from app.email.base import TransactionalEmailProvider
from app.email.exceptions import (
    EmailProviderConfigurationError,
    EmailProviderError,
)
from app.email.types import EmailDelivery, EmailMessage


class ResendEmailProvider(TransactionalEmailProvider):
    """Transactional email delivery through Resend's HTTPS API."""

    api_url = "https://api.resend.com/emails"
    user_agent = "arima-executive-os/0.1"

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self._client = client

    @property
    def provider_name(self) -> str:
        return "resend"

    async def send(self, message: EmailMessage) -> EmailDelivery:
        api_key = self._api_key()
        from_address = self._from_address()
        payload = {
            "from": from_address,
            "to": [message.to_address],
            "subject": message.subject,
            "text": message.text_body,
            "html": message.html_body,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "User-Agent": self.user_agent,
        }

        if self._client is not None:
            response = await self._post(self._client, headers, payload)
        else:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await self._post(client, headers, payload)

        return EmailDelivery(
            provider=self.provider_name,
            message_id=self._response_message_id(response),
        )

    def _api_key(self) -> str:
        api_key = self.settings.resend_api_key
        value = api_key.get_secret_value().strip() if api_key is not None else ""
        if not value:
            raise EmailProviderConfigurationError(
                "Resend email settings are incomplete"
            )
        return value

    def _from_address(self) -> str:
        email_address = self.settings.email_from_address
        if email_address is None or not self.settings.email_from_name.strip():
            raise EmailProviderConfigurationError(
                "Resend email settings are incomplete"
            )
        return formataddr((self.settings.email_from_name, str(email_address)))

    async def _post(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        payload: Mapping[str, object],
    ) -> httpx.Response:
        try:
            response = await client.post(
                self.api_url,
                headers=headers,
                json=payload,
            )
        except httpx.HTTPError as error:
            raise EmailProviderError("Resend delivery request failed") from error
        if not response.is_success:
            raise EmailProviderError(
                f"Resend API rejected delivery (HTTP {response.status_code})"
            )
        return response

    @staticmethod
    def _response_message_id(response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError as error:
            raise EmailProviderError(
                "Resend API returned an invalid delivery response"
            ) from error
        if not isinstance(payload, dict):
            raise EmailProviderError(
                "Resend API returned an invalid delivery response"
            )
        message_id = payload.get("id")
        if not isinstance(message_id, str) or not message_id.strip():
            raise EmailProviderError(
                "Resend API returned an invalid delivery response"
            )
        return message_id
