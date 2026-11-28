"""Environment-backed application configuration."""

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.domain.identity import normalize_principal_subject


class Settings(BaseSettings):
    """Settings used by the read-only backend milestone."""

    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parents[3] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )

    app_env: str = "development"
    log_level: str = "INFO"
    flagsmith_api_url: str = "https://api.flagsmith.com"
    flagsmith_api_key: SecretStr | None = None
    flagsmith_timeout_seconds: float = Field(default=10.0, gt=0, le=120)
    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-3.5-flash-lite"
    llm_timeout_seconds: float = Field(default=20.0, gt=0, le=120)
    database_url: SecretStr | None = None
    local_owner_subject: str | None = None

    def __init__(self, **values):
        super().__init__(**values)
        self._validate_database_url()

    def _validate_database_url(self) -> None:
        value = self.database_url
        if value is None:
            return
        raw = value.get_secret_value().strip()
        if not raw:
            self.database_url = None
            return
        try:
            parsed = urlsplit(raw)
            valid = (
                parsed.scheme == "postgresql+psycopg"
                and parsed.hostname is not None
                and parsed.path.strip("/") != ""
                and not parsed.fragment
            )
            # Accessing .port validates malformed or out-of-range port values.
            if parsed.port is not None and not 1 <= parsed.port <= 65535:
                valid = False
        except ValueError:
            valid = False
        if not valid:
            # Avoid Pydantic's normal validation error, which echoes raw input.
            raise ValueError("DATABASE_URL must be a valid postgresql+psycopg URL with a host and database") from None

    @field_validator("flagsmith_api_url")
    @classmethod
    def validate_api_url(cls, value: str) -> str:
        value = value.rstrip("/")
        if not value.startswith(("https://", "http://localhost", "http://127.0.0.1")):
            raise ValueError("FLAGSMITH_API_URL must use HTTPS (HTTP is allowed only for localhost)")
        return value

    @field_validator("local_owner_subject", mode="before")
    @classmethod
    def validate_local_owner_subject(cls, value: object) -> str | None:
        if value is None:
            return None
        try:
            return normalize_principal_subject(value)
        except ValueError:
            raise ValueError(
                "LOCAL_OWNER_SUBJECT must be a non-empty printable subject of at most 255 characters."
            ) from None


@lru_cache
def get_settings() -> Settings:
    """Return cached settings for the running process."""
    return Settings()
