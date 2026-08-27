from functools import lru_cache
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import (
    AliasChoices,
    EmailStr,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

SUPPORTED_TRANSACTIONAL_EMAIL_PROVIDERS = frozenset({"resend", "smtp"})
LOCAL_SMTP_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


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
    integration_encryption_key: SecretStr | None = None
    microsoft_client_id: str = "d3bed67e-bb34-4392-8a65-edf68fb50775"
    microsoft_authority: str = "https://login.microsoftonline.com/common"
    microsoft_redirect_uri: str = ""
    microsoft_integration_enabled: bool = False
    frontend_url: str = "http://localhost:3000"
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )
    trusted_hosts: list[str] = Field(default_factory=lambda: ["*"])
    trusted_proxy_ips: list[str] = Field(default_factory=list)
    platform_operator_user_ids: list[UUID] = Field(default_factory=list)
    # Founder access is an explicit, server-side email allowlist. An empty
    # value is intentionally handled as deny-all by the dependency rather
    # than preventing an otherwise healthy deployment from starting.
    founder_control_emails: Annotated[list[EmailStr], NoDecode] = Field(
        default_factory=list
    )
    privileged_mfa_required: bool = True
    privileged_mfa_lockout_minutes: int = Field(default=15, ge=1, le=1_440)
    privileged_mfa_max_attempts: int = Field(default=5, ge=1, le=20)
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
    withdrawal_intake_rate_limit_per_minute: int = Field(
        default=3, ge=1, le=100
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
    resend_api_key: SecretStr | None = None
    database_pool_size: int = Field(default=10, ge=1, le=100)
    database_max_overflow: int = Field(default=20, ge=0, le=200)
    database_pool_recycle_seconds: int = Field(
        default=1_800, ge=60, le=86_400
    )
    database_pool_timeout_seconds: float = Field(
        default=10.0, ge=0.1, le=120.0
    )
    database_connect_timeout_seconds: float = Field(
        default=10.0, ge=0.1, le=120.0
    )
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    dashboard_cache_ttl_seconds: int = Field(
        default=60,
        ge=1,
        le=3600,
    )
    market_data_provider: Literal["twelve_data", "alpha_vantage"] = "twelve_data"
    market_data_source: Literal["twelve_data", "alpha_vantage"] = "twelve_data"
    market_data_fallback_providers: list[Literal["twelve_data", "alpha_vantage"]] = Field(default_factory=list)
    market_data_account_plan: Literal[
        "basic",
        "grow",
        "pro",
        "ultra",
        "venture",
        "enterprise",
        "custom",
    ] = "basic"
    market_data_usage_scope: Literal[
        "internal_non_display",
        "customer_display",
        "redistribution",
    ] = "internal_non_display"
    market_data_customer_display_entitled: bool = False
    market_data_redistribution_entitled: bool = False
    market_data_real_time_entitled: bool = False
    market_data_entitlement_reference: SecretStr | None = None
    twelve_data_api_key: SecretStr | None = None
    twelve_data_base_url: str = "https://api.twelvedata.com"
    alpha_vantage_api_key: SecretStr | None = None
    alpha_vantage_base_url: str = "https://www.alphavantage.co"
    market_data_xauusd_symbol: str = Field(
        default="XAU/USD", min_length=3, max_length=50
    )
    market_data_btcusd_symbol: str = Field(
        default="BTC/USD", min_length=3, max_length=50
    )
    market_data_xauusd_exchange: str = Field(
        default="Commodity", min_length=1, max_length=50
    )
    market_data_btcusd_exchange: str = Field(
        default="Coinbase Pro", min_length=1, max_length=50
    )
    market_data_spx_symbol: str = Field(
        default="SPX", min_length=1, max_length=50
    )
    market_data_spx_exchange: str = Field(
        default="CBOE", min_length=1, max_length=50
    )
    market_data_stale_after_seconds: int = Field(
        default=120, ge=1, le=86_400
    )
    market_data_timeout_seconds: float = Field(
        default=5.0, ge=0.1, le=30.0
    )
    market_data_rate_limit_per_minute: int = Field(
        default=8, ge=4, le=10_000
    )
    openai_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    ai_provider_timeout_seconds: float = Field(
        default=15.0, ge=1.0, le=60.0
    )
    gemini_model: str = Field(
        default="gemini-3.6-flash",
        min_length=1,
        max_length=200,
    )
    nvidia_api_key: SecretStr | None = None
    deepseek_api_key: SecretStr | None = None
    deepseek_model: str = Field(
        default="deepseek-v4-flash",
        min_length=1,
        max_length=200,
    )
    groq_api_key: SecretStr | None = None
    groq_model: str = Field(
        default="openai/gpt-oss-20b",
        min_length=1,
        max_length=200,
    )
    ollama_url: str = "http://localhost:11434"
    default_provider: Literal[
        "mock",
        "openai",
        "anthropic",
        "gemini",
        "nvidia",
        "deepseek",
        "groq",
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
    arima_voice_execution_timeout_seconds: float = Field(
        default=35.0, ge=1.0, le=60.0
    )
    arima_voice_max_provider_retries: int = Field(
        default=1, ge=0, le=2
    )
    azure_speech_enabled: bool = False
    azure_speech_region: str = Field(default="uksouth", min_length=1, max_length=40)
    azure_speech_key: SecretStr | None = None
    azure_speech_output_format: str = Field(
        default="audio-24khz-48kbitrate-mono-mp3", min_length=1, max_length=100
    )
    azure_speech_timeout_seconds: float = Field(default=10.0, ge=1.0, le=30.0)
    voice_transcript_rate_limit_per_minute: int = Field(
        default=10, ge=1, le=1_000
    )
    voice_tts_rate_limit_per_minute: int = Field(default=10, ge=1, le=1_000)
    telegram_enabled: bool = False
    telegram_bot_token: SecretStr | None = Field(default=None, min_length=1)
    telegram_webhook_secret: SecretStr | None = Field(
        default=None, min_length=32, max_length=256
    )
    ai_execution_enabled: bool = True
    document_storage_max_bytes: int = Field(default=25 * 1024 * 1024, ge=1, le=100 * 1024 * 1024)
    r2_endpoint_url: str | None = None
    r2_bucket: str | None = None
    r2_access_key_id: str | None = None
    r2_secret_access_key: SecretStr | None = None

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
        "founder_control_emails",
        mode="before",
    )
    @classmethod
    def parse_delimited_list(cls, value: Any) -> Any:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("email_provider")
    @classmethod
    def normalize_email_provider(cls, value: str | None) -> str | None:
        if value is None:
            return None
        provider = value.strip().lower()
        if not provider:
            return None
        if provider not in SUPPORTED_TRANSACTIONAL_EMAIL_PROVIDERS:
            supported = ", ".join(sorted(SUPPORTED_TRANSACTIONAL_EMAIL_PROVIDERS))
            raise ValueError(f"EMAIL_PROVIDER must be one of: {supported}")
        return provider

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
        if self.telegram_enabled:
            telegram_secrets = (
                self.telegram_bot_token,
                self.telegram_webhook_secret,
            )
            if any(
                secret is None or not secret.get_secret_value().strip()
                for secret in telegram_secrets
            ):
                raise ValueError(
                    "Telegram transport requires server-side bot and "
                    "webhook credentials"
                )
        if self.environment == "production":
            if self.microsoft_integration_enabled and (
                self.integration_encryption_key is None
                or not self.integration_encryption_key.get_secret_value().strip()
            ):
                raise ValueError("INTEGRATION_ENCRYPTION_KEY must be configured in production")
            redirect = urlsplit(self.microsoft_redirect_uri)
            if self.microsoft_integration_enabled and (
                redirect.scheme != "https"
                or not redirect.hostname
                or redirect.username is not None
                or redirect.password is not None
                or not redirect.path.endswith("/api/v1/integrations/microsoft/callback")
            ):
                raise ValueError("MICROSOFT_REDIRECT_URI must be an HTTPS callback URI in production")
            frontend = urlsplit(self.frontend_url)
            if (
                frontend.scheme != "https"
                or not frontend.hostname
                or frontend.username is not None
                or frontend.password is not None
                or frontend.hostname.lower() in LOCAL_SMTP_HOSTS
            ):
                raise ValueError(
                    "FRONTEND_URL must be an external HTTPS URL in production"
                )
            if not self.auth_cookie_secure:
                raise ValueError(
                    "AUTH_COOKIE_SECURE must be enabled in production"
                )
            if not self.cors_origins or "*" in self.cors_origins:
                raise ValueError(
                    "CORS_ORIGINS must contain explicit production origins"
                )
            frontend_origin = f"{frontend.scheme}://{frontend.netloc}"
            for origin in self.cors_origins:
                parsed_origin = urlsplit(origin)
                if (
                    parsed_origin.scheme != "https"
                    or not parsed_origin.hostname
                    or parsed_origin.username is not None
                    or parsed_origin.password is not None
                    or parsed_origin.path not in {"", "/"}
                    or parsed_origin.query
                    or parsed_origin.fragment
                ):
                    raise ValueError(
                        "CORS_ORIGINS must contain HTTPS origins only in production"
                    )
            if frontend_origin not in {
                origin.rstrip("/") for origin in self.cors_origins
            }:
                raise ValueError(
                    "CORS_ORIGINS must include the FRONTEND_URL origin"
                )
            if not self.trusted_hosts or "*" in self.trusted_hosts:
                raise ValueError(
                    "TRUSTED_HOSTS must contain explicit production hosts"
                )
            if not self.email_provider:
                raise ValueError(
                    "EMAIL_PROVIDER must be configured in production"
                )
            if self.email_provider == "resend":
                if not self.email_from_address:
                    raise ValueError(
                        "SMTP_FROM_EMAIL (or EMAIL_FROM_ADDRESS) must be "
                        "configured in production"
                    )
                if not self.email_from_name.strip():
                    raise ValueError(
                        "SMTP_FROM_NAME (or EMAIL_FROM_NAME) must not be blank"
                    )
                if (
                    self.resend_api_key is None
                    or not self.resend_api_key.get_secret_value().strip()
                ):
                    raise ValueError(
                        "RESEND_API_KEY must be configured in production"
                    )
            if self.email_provider == "smtp":
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
                if (
                    self.smtp_host is not None
                    and self.smtp_host.strip().lower() in LOCAL_SMTP_HOSTS
                ):
                    raise ValueError(
                        "SMTP_HOST must reference an external SMTP provider "
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
            if self.ai_execution_enabled and self.default_provider == "mock":
                raise ValueError(
                    "DEFAULT_PROVIDER must not use the mock provider in production"
                )
            provider_credentials = {
                "openai": self.openai_api_key,
                "anthropic": self.anthropic_api_key,
                "gemini": self.gemini_api_key,
                "nvidia": self.nvidia_api_key,
                "deepseek": self.deepseek_api_key,
                "groq": self.groq_api_key,
            }
            credential = provider_credentials.get(self.default_provider)
            if (
                self.ai_execution_enabled
                and self.default_provider in provider_credentials
                and (
                    credential is None
                    or not credential.get_secret_value().strip()
                )
            ):
                raise ValueError(
                    f"{self.default_provider.upper()}_API_KEY must be configured "
                    "for the production default provider"
                )
            if self.ai_execution_enabled and self.default_provider == "ollama":
                ollama = urlsplit(self.ollama_url)
                if (
                    ollama.scheme != "https"
                    or not ollama.hostname
                    or ollama.hostname.lower() in LOCAL_SMTP_HOSTS
                ):
                    raise ValueError(
                        "OLLAMA_URL must be an external HTTPS URL in production"
                    )
        if self.max_output_tokens > self.max_model_tokens:
            raise ValueError(
                "MAX_OUTPUT_TOKENS cannot exceed MAX_MODEL_TOKENS"
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
