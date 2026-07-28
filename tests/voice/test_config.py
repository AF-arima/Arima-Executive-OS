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


def test_voice_settings_validate_bounds() -> None:
    with pytest.raises(ValidationError):
        Settings(arima_voice_max_transcript_length=0)
