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
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
