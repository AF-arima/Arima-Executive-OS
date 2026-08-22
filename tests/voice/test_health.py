from pydantic import SecretStr

from app.core.config import Settings
from app.voice.health import configured_provider_provenance, voice_health


def test_voice_health_exposes_each_provenance_state() -> None:
    assert voice_health(
        enabled=True,
        orchestration_available=True,
        provider_provenance="verified",
    ).provider_provenance == "verified"
    assert voice_health(
        enabled=True,
        orchestration_available=True,
        provider_provenance="mock",
    ).provider_provenance == "mock"
    assert voice_health(
        enabled=True,
        orchestration_available=False,
        provider_provenance="unverified",
    ).provider_provenance == "unverified"


def test_provider_provenance_is_mock_by_default() -> None:
    settings = Settings(_env_file=None)

    assert configured_provider_provenance(settings) == "mock"


def test_provider_provenance_requires_ai_execution_and_credentials() -> None:
    disabled = Settings(_env_file=None, ai_execution_enabled=False)
    missing_credentials = Settings(
        _env_file=None,
        default_provider="openai",
        openai_api_key=None,
    )
    configured = Settings(
        _env_file=None,
        default_provider="openai",
        openai_api_key=SecretStr("configured-test-key"),
    )

    assert configured_provider_provenance(disabled) == "unverified"
    assert configured_provider_provenance(missing_credentials) == "unverified"
    assert configured_provider_provenance(configured) == "verified"
