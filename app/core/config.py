from functools import lru_cache
from typing import Any, Literal
from uuid import UUID

from pydantic import (
    AliasChoices,
    EmailStr,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)
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
    session_refresh_token_expire_hours: int = Field(default=24, gt=0)
    jwt_issuer: str = Field(default="arima-executive-os", min_length=1)
    jwt_audience: str = Field(default="arima-executive-web", min_length=1)
    security_token_secret: SecretStr = Field(
        default=SecretStr(
            "development-only-security-token-secret-change-me"
        ),
        min_length=32,
    )
    frontend_url: str = "http://localhost:3000"
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )
    trusted_hosts: list[str] = Field(default_factory=lambda: ["*"])
    trusted_proxy_ips: list[str] = Field(default_factory=list)
    platform_operator_user_ids: list[UUID] = Field(default_factory=list)
    auth_cookie_domain: str | None = None
    auth_cookie_secure: bool = False
    auth_cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    auth_refresh_cookie_name: str = "arima_refresh_token"
    auth_csrf_cookie_name: str = "arima_csrf_token"
    csrf_header_name: str = "X-CSRF-Token"
    max_failed_login_attempts: int = Field(default=5, ge=1, le=20)
    account_lockout_minutes: int = Field(default=15, ge=1, le=1_440)
    login_rate_limit_per_minute: int = Field(default=10, ge=1, le=1_000)
    registration_rate_limit_per_hour: int = Field(
        default=10, ge=1, le=1_000
    )
    password_reset_rate_limit_per_hour: int = Field(
        default=5, ge=1, le=1_000
    )
    verification_token_expire_hours: int = Field(default=24, ge=1, le=168)
    password_reset_token_expire_minutes: int = Field(
        default=60, ge=5, le=1_440
    )
    email_change_token_expire_hours: int = Field(default=24, ge=1, le=168)
    email_provider: str | None = None
    email_from_address: EmailStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "SMTP_FROM_EMAIL",
            "EMAIL_FROM_ADDRESS",
        ),
    )
    email_from_name: str = Field(
        default="Arima Executive OS",
        min_length=1,
        validation_alias=AliasChoices(
            "SMTP_FROM_NAME",
            "EMAIL_FROM_NAME",
        ),
    )
    smtp_host: str | None = None
    smtp_port: int = Field(default=587, ge=1, le=65_535)
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_use_tls: bool = True
    smtp_use_ssl: bool = False
    database_pool_size: int = Field(default=10, ge=1, le=100)
    database_max_overflow: int = Field(default=20, ge=0, le=200)
    database_pool_recycle_seconds: int = Field(
        default=1_800, ge=60, le=86_400
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
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
    arima_voice_enabled: bool = True
    arima_voice_default_language: str = Field(
        default="en", min_length=2, max_length=20
    )
    arima_voice_default_locale: str = Field(
        default="en-GB", min_length=2, max_length=35
    )
    arima_voice_max_transcript_length: int = Field(
        default=10_000, ge=1, le=100_000
    )
    arima_voice_session_timeout_seconds: int = Field(
        default=1_800, ge=60, le=86_400
    )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    @field_validator(
        "cors_origins",
        "trusted_hosts",
        "trusted_proxy_ips",
        "platform_operator_user_ids",
        mode="before",
    )
    @classmethod
    def parse_delimited_list(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

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
        development_token_secret = (
            "development-only-security-token-secret-change-me"
        )
        if (
            self.environment == "production"
            and self.security_token_secret.get_secret_value()
            == development_token_secret
        ):
            raise ValueError(
                "SECURITY_TOKEN_SECRET must be configured in production"
            )
        if self.auth_cookie_samesite == "none" and not self.auth_cookie_secure:
            raise ValueError(
                "AUTH_COOKIE_SECURE must be enabled when SameSite=None"
            )
        if self.smtp_use_ssl and self.smtp_use_tls:
            raise ValueError("SMTP_USE_SSL and SMTP_USE_TLS cannot both be enabled")
        if self.environment == "production":
            if not self.auth_cookie_secure:
                raise ValueError(
                    "AUTH_COOKIE_SECURE must be enabled in production"
                )
            if not self.cors_origins or "*" in self.cors_origins:
                raise ValueError(
                    "CORS_ORIGINS must contain explicit production origins"
                )
            if not self.trusted_hosts or "*" in self.trusted_hosts:
                raise ValueError(
                    "TRUSTED_HOSTS must contain explicit production hosts"
                )
            if not self.email_provider:
                raise ValueError(
                    "EMAIL_PROVIDER must be configured in production"
                )
            if self.email_provider.strip().lower() == "smtp":
                if not all(
                    (
                        self.email_from_address,
                        self.smtp_host,
                        self.smtp_username,
                        self.smtp_password,
                    )
                ):
                    raise ValueError(
                        "SMTP_FROM_EMAIL (or EMAIL_FROM_ADDRESS), SMTP_HOST, "
                        "SMTP_USERNAME, and SMTP_PASSWORD must be configured "
                        "in production"
                    )
                if not self.email_from_name.strip():
                    raise ValueError(
                        "SMTP_FROM_NAME (or EMAIL_FROM_NAME) must not be blank"
                    )
                if not (self.smtp_use_tls or self.smtp_use_ssl):
                    raise ValueError(
                        "SMTP transport encryption must be enabled in production"
                    )
            if "*" in self.trusted_proxy_ips:
                raise ValueError(
                    "TRUSTED_PROXY_IPS must not contain a wildcard in production"
                )
        if self.max_output_tokens > self.max_model_tokens:
            raise ValueError(
                "MAX_OUTPUT_TOKENS cannot exceed MAX_MODEL_TOKENS"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
