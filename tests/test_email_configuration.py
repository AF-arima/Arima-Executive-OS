import asyncio
from email.message import EmailMessage as MimeEmailMessage
import json
from unittest.mock import patch

import httpx
import pytest
from fastapi import Request
from pydantic import ValidationError

from app.auth.exceptions import EmailDeliveryError
from app.core import errors
from app.core.config import Settings, get_settings
from app.email.exceptions import EmailProviderError
from app.email.factory import get_transactional_email_service
from app.email.providers.resend import ResendEmailProvider
from app.email.providers.smtp import SmtpEmailProvider
from app.email.types import EmailMessage

SENDER_VARIABLES = (
    "SMTP_FROM_EMAIL",
    "SMTP_FROM_NAME",
    "EMAIL_FROM_ADDRESS",
    "EMAIL_FROM_NAME",
)


def _clear_sender_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable in SENDER_VARIABLES:
        monkeypatch.delenv(variable, raising=False)


def test_settings_accepts_canonical_smtp_sender_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_sender_variables(monkeypatch)
    monkeypatch.setenv("SMTP_FROM_EMAIL", "sender@example.com")
    monkeypatch.setenv("SMTP_FROM_NAME", "Arima Executive OS")

    settings = Settings(_env_file=None)

    assert settings.email_from_address == "sender@example.com"
    assert settings.email_from_name == "Arima Executive OS"


def test_settings_accepts_legacy_sender_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_sender_variables(monkeypatch)
    monkeypatch.setenv("EMAIL_FROM_ADDRESS", "legacy@example.com")
    monkeypatch.setenv("EMAIL_FROM_NAME", "Legacy Arima")

    settings = Settings(_env_file=None)

    assert settings.email_from_address == "legacy@example.com"
    assert settings.email_from_name == "Legacy Arima"


def test_canonical_smtp_sender_variables_take_precedence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_sender_variables(monkeypatch)
    monkeypatch.setenv("SMTP_FROM_EMAIL", "canonical@example.com")
    monkeypatch.setenv("SMTP_FROM_NAME", "Canonical Arima")
    monkeypatch.setenv("EMAIL_FROM_ADDRESS", "legacy@example.com")
    monkeypatch.setenv("EMAIL_FROM_NAME", "Legacy Arima")

    settings = Settings(_env_file=None)

    assert settings.email_from_address == "canonical@example.com"
    assert settings.email_from_name == "Canonical Arima"


def test_production_smtp_requires_a_sender_address() -> None:
    production_values = {
        "environment": "production",
        "jwt_secret_key": "a" * 32,
        "security_token_secret": "b" * 32,
        "frontend_url": "https://frontend.example",
        "auth_cookie_secure": True,
        "cors_origins": ["https://frontend.example"],
        "trusted_hosts": ["api.example"],
        "email_provider": "smtp",
        "smtp_host": "smtp.example",
        "smtp_username": "smtp-user",
        "smtp_password": "smtp-password",
        "smtp_use_tls": True,
        "smtp_use_ssl": False,
        "default_provider": "openai",
        "openai_api_key": "configured",
    }

    with pytest.raises(ValidationError, match="SMTP_FROM_EMAIL"):
        Settings(_env_file=None, **production_values)

    settings = Settings(
        _env_file=None,
        **production_values,
        SMTP_FROM_EMAIL="sender@example.com",
        SMTP_FROM_NAME="Arima Executive OS",
    )

    assert settings.email_from_address == "sender@example.com"
    assert settings.email_from_name == "Arima Executive OS"


def test_email_provider_must_be_registered() -> None:
    with pytest.raises(
        ValidationError,
        match="EMAIL_PROVIDER must be one of: resend, smtp",
    ):
        Settings(_env_file=None, email_provider="unknown-provider")


def test_production_resend_requires_an_api_key_and_sender() -> None:
    production_values = {
        "environment": "production",
        "jwt_secret_key": "a" * 32,
        "security_token_secret": "b" * 32,
        "frontend_url": "https://frontend.example",
        "auth_cookie_secure": True,
        "cors_origins": ["https://frontend.example"],
        "trusted_hosts": ["api.example"],
        "email_provider": "resend",
        "SMTP_FROM_EMAIL": "sender@example.com",
        "SMTP_FROM_NAME": "Arima Executive OS",
        "default_provider": "openai",
        "openai_api_key": "configured",
    }

    with pytest.raises(ValidationError, match="RESEND_API_KEY"):
        Settings(_env_file=None, **production_values)

    settings = Settings(
        _env_file=None,
        **production_values,
        resend_api_key="resend-api-key",
    )

    assert settings.email_provider == "resend"
    assert settings.resend_api_key is not None


def test_resend_provider_posts_the_existing_template_payload() -> None:
    settings = Settings(
        _env_file=None,
        SMTP_FROM_EMAIL="sender@example.com",
        SMTP_FROM_NAME="Arima Executive OS",
        resend_api_key=" resend-api-key ",
    )
    captured_request: httpx.Request | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_request
        captured_request = request
        return httpx.Response(200, json={"id": "resend-message-id"})

    async def exercise() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            provider = ResendEmailProvider(settings, client=client)
            delivery = await provider.send(
                EmailMessage(
                    to_address="recipient@example.com",
                    subject="Verification",
                    text_body="Plain text",
                    html_body="<p>HTML</p>",
                )
            )

        assert delivery.provider == "resend"
        assert delivery.message_id == "resend-message-id"

    asyncio.run(exercise())

    assert captured_request is not None
    assert captured_request.method == "POST"
    assert str(captured_request.url) == "https://api.resend.com/emails"
    assert captured_request.headers["Authorization"] == "Bearer resend-api-key"
    assert captured_request.headers["User-Agent"] == "arima-executive-os/0.1"
    assert captured_request.headers["Content-Type"] == "application/json"
    assert json.loads(captured_request.content) == {
        "from": "Arima Executive OS <sender@example.com>",
        "to": ["recipient@example.com"],
        "subject": "Verification",
        "text": "Plain text",
        "html": "<p>HTML</p>",
    }


@pytest.mark.parametrize(
    ("response", "expected_message"),
    [
        (httpx.Response(403, json={"message": "invalid API key"}), "HTTP 403"),
        (httpx.Response(200, json={}), "invalid delivery response"),
    ],
)
def test_resend_provider_wraps_unsafe_delivery_responses(
    response: httpx.Response,
    expected_message: str,
) -> None:
    settings = Settings(
        _env_file=None,
        SMTP_FROM_EMAIL="sender@example.com",
        SMTP_FROM_NAME="Arima Executive OS",
        resend_api_key="resend-api-key",
    )

    async def exercise() -> None:
        transport = httpx.MockTransport(lambda request: response)
        async with httpx.AsyncClient(transport=transport) as client:
            provider = ResendEmailProvider(settings, client=client)
            with pytest.raises(EmailProviderError, match=expected_message) as error:
                await provider.send(
                    EmailMessage(
                        to_address="recipient@example.com",
                        subject="Verification",
                        text_body="Plain text",
                        html_body="<p>HTML</p>",
                    )
                )
        assert "resend-api-key" not in str(error.value)

    asyncio.run(exercise())


def test_resend_provider_wraps_network_failures() -> None:
    settings = Settings(
        _env_file=None,
        SMTP_FROM_EMAIL="sender@example.com",
        SMTP_FROM_NAME="Arima Executive OS",
        resend_api_key="resend-api-key",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out", request=request)

    async def exercise() -> None:
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            provider = ResendEmailProvider(settings, client=client)
            with pytest.raises(
                EmailProviderError,
                match="Resend delivery request failed",
            ):
                await provider.send(
                    EmailMessage(
                        to_address="recipient@example.com",
                        subject="Verification",
                        text_body="Plain text",
                        html_body="<p>HTML</p>",
                    )
                )

    asyncio.run(exercise())


def test_factory_builds_the_resend_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.setenv("EMAIL_PROVIDER", "resend")
    monkeypatch.setenv("RESEND_API_KEY", "resend-api-key")
    monkeypatch.setenv("SMTP_FROM_EMAIL", "sender@example.com")
    monkeypatch.setenv("SMTP_FROM_NAME", "Arima Executive OS")
    get_settings.cache_clear()
    get_transactional_email_service.cache_clear()
    try:
        service = get_transactional_email_service()
        assert isinstance(service.provider, ResendEmailProvider)
    finally:
        get_settings.cache_clear()
        get_transactional_email_service.cache_clear()


def test_production_smtp_rejects_localhost() -> None:
    production_values = {
        "environment": "production",
        "jwt_secret_key": "a" * 32,
        "security_token_secret": "b" * 32,
        "frontend_url": "https://frontend.example",
        "auth_cookie_secure": True,
        "cors_origins": ["https://frontend.example"],
        "trusted_hosts": ["api.example"],
        "email_provider": "smtp",
        "smtp_host": "localhost",
        "smtp_username": "smtp-user",
        "smtp_password": "smtp-password",
        "smtp_use_tls": True,
        "smtp_use_ssl": False,
        "SMTP_FROM_EMAIL": "sender@example.com",
        "default_provider": "openai",
        "openai_api_key": "configured",
    }

    with pytest.raises(ValidationError, match="SMTP_HOST must reference"):
        Settings(_env_file=None, **production_values)


def test_smtp_provider_uses_the_configured_sender() -> None:
    settings = Settings(
        _env_file=None,
        SMTP_FROM_EMAIL="sender@example.com",
        SMTP_FROM_NAME="Arima Executive OS",
        smtp_host="smtp.example",
        smtp_username="smtp-user",
        smtp_password="smtp-password",
        smtp_use_tls=False,
    )

    class RecordingSMTP:
        sent_message: MimeEmailMessage | None = None

        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def __enter__(self) -> "RecordingSMTP":
            return self

        def __exit__(self, *args: object) -> None:
            return None

        def ehlo(self) -> None:
            return None

        def login(self, username: str, password: str) -> None:
            assert username == "smtp-user"
            assert password == "smtp-password"

        def send_message(self, message: MimeEmailMessage) -> None:
            self.sent_message = message
            type(self).sent_message = message

    provider = SmtpEmailProvider(settings)
    with patch("app.email.providers.smtp.smtplib.SMTP", RecordingSMTP):
        provider._send_sync(
            EmailMessage(
                to_address="recipient@example.com",
                subject="Verification",
                text_body="Plain text",
                html_body="<p>HTML</p>",
            )
        )

    assert RecordingSMTP.sent_message is not None
    assert RecordingSMTP.sent_message["From"] == (
        "Arima Executive OS <sender@example.com>"
    )


def test_email_delivery_error_is_logged_once_with_its_cause() -> None:
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "https",
        "path": "/api/v1/auth/register",
        "raw_path": b"/api/v1/auth/register",
        "query_string": b"",
        "headers": [],
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 443),
    }

    async def exercise() -> None:
        request = Request(scope)
        request.state.correlation_id = "test-correlation-id"
        captured_error: EmailDeliveryError | None = None
        try:
            try:
                raise EmailProviderError("SMTP delivery failed")
            except EmailProviderError as provider_error:
                raise EmailDeliveryError(
                    "Transactional email delivery failed"
                ) from provider_error
        except EmailDeliveryError as delivery_error:
            captured_error = delivery_error
            with patch.object(errors.logger, "error") as email_error:
                response = await errors._email_delivery_handler(
                    request,
                    delivery_error,
                )

        assert response.status_code == 503
        email_error.assert_called_once()
        arguments, keyword_arguments = email_error.call_args
        assert arguments == ("email_delivery_failed",)
        assert keyword_arguments["exc_info"] is captured_error
        assert keyword_arguments["extra"] == {
            "correlation_id": "test-correlation-id",
            "method": "POST",
            "path": "/api/v1/auth/register",
        }

    asyncio.run(exercise())
