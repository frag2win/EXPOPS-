"""Environment-backed application configuration."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Settings used by the read-only backend milestone."""

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[3] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_env: str = "development"
    log_level: str = "INFO"
    flagsmith_api_url: str = "https://api.flagsmith.com"
    flagsmith_api_key: SecretStr | None = None
    flagsmith_timeout_seconds: float = Field(default=10.0, gt=0, le=120)

    @field_validator("flagsmith_api_url")
    @classmethod
    def validate_api_url(cls, value: str) -> str:
        value = value.rstrip("/")
        if not value.startswith(("https://", "http://localhost", "http://127.0.0.1")):
            raise ValueError("FLAGSMITH_API_URL must use HTTPS (HTTP is allowed only for localhost)")
        return value


@lru_cache
def get_settings() -> Settings:
    """Return cached settings for the running process."""
    return Settings()
