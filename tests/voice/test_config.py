import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_voice_settings_have_safe_defaults() -> None:
    settings = Settings()
    assert settings.arima_voice_enabled is True
    assert settings.arima_voice_default_language == "en"
    assert settings.arima_voice_default_locale == "en-GB"
    assert settings.arima_voice_max_transcript_length == 10_000
    assert settings.arima_voice_session_timeout_seconds == 1_800
    assert settings.arima_voice_execution_timeout_seconds == 35.0
    assert settings.arima_voice_max_provider_retries == 1
    assert settings.ai_provider_timeout_seconds == 15.0


def test_voice_execution_budget_covers_one_retry_with_margin() -> None:
    settings = Settings()
    required = settings.ai_provider_timeout_seconds * (
        settings.arima_voice_max_provider_retries + 1
    )
    assert settings.arima_voice_execution_timeout_seconds > required


def test_voice_settings_validate_bounds() -> None:
    with pytest.raises(ValidationError):
        Settings(arima_voice_max_transcript_length=0)
