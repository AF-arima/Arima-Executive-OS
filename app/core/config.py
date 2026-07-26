from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    environment: Literal["development", "test", "production"] = "development"
    app_name: str = "Arima Executive OS"
    database_url: str = (
        "postgresql+asyncpg://postgres:postgres@localhost/arima_executive_os"
    )
    jwt_secret_key: SecretStr = Field(
        default=SecretStr(
            "development-only-change-me-before-production"
        ),
        min_length=32,
    )
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    access_token_expire_minutes: int = Field(default=15, gt=0)
    refresh_token_expire_days: int = Field(default=30, gt=0)
    dashboard_cache_ttl_seconds: int = Field(
        default=60,
        ge=1,
        le=3600,
    )
    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    nvidia_api_key: SecretStr | None = None
    ollama_url: str = "http://localhost:11434"
    default_provider: Literal[
        "mock",
        "openai",
        "anthropic",
        "nvidia",
        "ollama",
    ] = "mock"
    default_model: str = Field(
        default="mock-model",
        min_length=1,
        max_length=200,
    )
    max_model_tokens: int = Field(default=128_000, ge=1)
    default_temperature: float = Field(default=0.2, ge=0, le=2)
    max_output_tokens: int = Field(default=4_096, ge=1)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @model_validator(mode="after")
    def reject_development_secret_in_production(self) -> "Settings":
        development_secret = (
            "development-only-change-me-before-production"
        )
        if (
            self.environment == "production"
            and self.jwt_secret_key.get_secret_value() == development_secret
        ):
            raise ValueError(
                "JWT_SECRET_KEY must be configured in production"
            )
        if self.max_output_tokens > self.max_model_tokens:
            raise ValueError(
                "MAX_OUTPUT_TOKENS cannot exceed MAX_MODEL_TOKENS"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
