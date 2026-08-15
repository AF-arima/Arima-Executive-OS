from pydantic import SecretStr, ValidationError
import pytest

from app.core.config import Settings


def test_telegram_is_disabled_and_credentials_are_secret_by_default() -> None:
    settings = Settings(_env_file=None)

    assert settings.telegram_enabled is False
    assert settings.telegram_bot_token is None
    assert settings.telegram_webhook_secret is None


def test_enabled_telegram_requires_both_server_side_credentials() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            telegram_enabled=True,
            telegram_bot_token=SecretStr("bot-secret"),
        )
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            telegram_enabled=True,
            telegram_bot_token=SecretStr("bot-secret"),
            telegram_webhook_secret=SecretStr("too-short"),
        )

    settings = Settings(
        _env_file=None,
        telegram_enabled=True,
        telegram_bot_token=SecretStr("bot-secret"),
        telegram_webhook_secret=SecretStr("w" * 40),
    )
    rendered = repr(settings)
    assert "bot-secret" not in rendered
    assert "w" * 40 not in rendered


def test_production_style_configuration_starts_with_telegram_disabled() -> None:
    settings = Settings(
        _env_file=None,
        environment="production",
        jwt_secret_key=SecretStr("j" * 40),
        security_token_secret=SecretStr("s" * 40),
        frontend_url="https://app.example.com",
        auth_cookie_secure=True,
        cors_origins=["https://app.example.com"],
        trusted_hosts=["api.example.com"],
        email_provider="resend",
        email_from_address="noreply@example.com",
        email_from_name="Arima",
        resend_api_key=SecretStr("configured"),
        default_provider="openai",
        openai_api_key=SecretStr("configured"),
        telegram_enabled=False,
    )

    assert settings.telegram_enabled is False
    assert settings.market_data_customer_display_entitled is False
    assert settings.market_data_redistribution_entitled is False


def test_production_configuration_can_disable_ai_fail_closed() -> None:
    settings = Settings(
        _env_file=None,
        environment="production",
        jwt_secret_key=SecretStr("j" * 40),
        security_token_secret=SecretStr("s" * 40),
        frontend_url="https://app.example.com",
        auth_cookie_secure=True,
        cors_origins=["https://app.example.com"],
        trusted_hosts=["api.example.com"],
        email_provider="resend",
        email_from_address="noreply@example.com",
        resend_api_key=SecretStr("configured"),
        ai_execution_enabled=False,
    )

    assert settings.ai_execution_enabled is False


def test_production_configuration_rejects_development_ai_and_frontend_defaults(
) -> None:
    values = {
        "environment": "production",
        "jwt_secret_key": SecretStr("j" * 40),
        "security_token_secret": SecretStr("s" * 40),
        "frontend_url": "https://app.example.com",
        "auth_cookie_secure": True,
        "cors_origins": ["https://app.example.com"],
        "trusted_hosts": ["api.example.com"],
        "email_provider": "resend",
        "email_from_address": "noreply@example.com",
        "resend_api_key": SecretStr("configured"),
    }

    with pytest.raises(ValidationError, match="mock provider"):
        Settings(_env_file=None, **values)
    with pytest.raises(ValidationError, match="external HTTPS"):
        Settings(
            _env_file=None,
            **{**values, "frontend_url": "http://localhost:3000"},
            default_provider="openai",
            openai_api_key=SecretStr("configured"),
        )
    with pytest.raises(ValidationError, match="OPENAI_API_KEY"):
        Settings(
            _env_file=None,
            **values,
            default_provider="openai",
        )
    with pytest.raises(ValidationError, match="include the FRONTEND_URL"):
        Settings(
            _env_file=None,
            **{**values, "cors_origins": ["https://other.example.com"]},
            default_provider="openai",
            openai_api_key=SecretStr("configured"),
        )
