from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Arima Executive OS"
    database_url: str = (
        "postgresql+asyncpg://postgres:postgres@localhost/arima_executive_os"
    )

    model_config = SettingsConfigDict(env_file=".env")


def get_settings() -> Settings:
    return Settings()
