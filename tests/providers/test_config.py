from pydantic import SecretStr, ValidationError
import pytest

from app.core.config import Settings
from app.providers import (
    ProviderCapabilities,
    ProviderConfig,
    ProviderName,
    ProviderPlatformConfig,
)


def test_settings_support_provider_environment_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "not-a-real-secret")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-secret")
    monkeypatch.setenv("GEMINI_API_KEY", "not-a-real-secret")
    monkeypatch.setenv("NVIDIA_API_KEY", "not-a-real-secret")
    monkeypatch.setenv("OLLAMA_URL", "http://localhost:9999")
    monkeypatch.setenv("DEFAULT_PROVIDER", "mock")
    monkeypatch.setenv("DEFAULT_MODEL", "deterministic-test-model")
    monkeypatch.setenv("MAX_MODEL_TOKENS", "8192")
    monkeypatch.setenv("DEFAULT_TEMPERATURE", "0.4")
    monkeypatch.setenv("MAX_OUTPUT_TOKENS", "1024")

    settings = Settings(_env_file=None)
    assert isinstance(settings.openai_api_key, SecretStr)
    assert isinstance(settings.anthropic_api_key, SecretStr)
    assert isinstance(settings.gemini_api_key, SecretStr)
    assert isinstance(settings.nvidia_api_key, SecretStr)
    assert settings.ollama_url == "http://localhost:9999"
    assert settings.default_provider == "mock"
    assert settings.default_model == "deterministic-test-model"
    assert settings.max_model_tokens == 8192
    assert settings.default_temperature == 0.4
    assert settings.max_output_tokens == 1024

    platform = ProviderPlatformConfig.from_settings(settings)
    assert platform.default_provider is ProviderName.MOCK
    assert platform.default_model == "deterministic-test-model"
    assert platform.for_provider(ProviderName.OPENAI).api_key is not None
    assert platform.for_provider(ProviderName.GEMINI).api_key is not None
    assert platform.for_provider(ProviderName.GEMINI).default_model == (
        "gemini-3.6-flash"
    )
    assert (
        platform.for_provider(ProviderName.OLLAMA).base_url
        == "http://localhost:9999"
    )


def test_settings_and_provider_config_validate_token_limits() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            max_model_tokens=100,
            max_output_tokens=101,
        )

    with pytest.raises(ValidationError):
        ProviderConfig(
            provider=ProviderName.MOCK,
            default_model="mock-model",
            max_model_tokens=100,
            default_temperature=0.2,
            max_output_tokens=101,
            capabilities=ProviderCapabilities(),
        )
