from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "Arima Executive OS"

    model_config = SettingsConfigDict(env_file=".env")


def get_settings() -> Settings:
    return Settings()