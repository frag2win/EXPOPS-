"""Configuration behavior and safety tests."""

import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_default_flagsmith_url_is_cloud_api():
    settings = Settings(_env_file=None)
    assert settings.flagsmith_api_url == "https://api.flagsmith.com"


def test_url_trailing_slash_is_normalized():
    assert Settings(_env_file=None, flagsmith_api_url="https://flags.example/").flagsmith_api_url == "https://flags.example"


@pytest.mark.parametrize("url", ["http://flags.example", "file:///tmp/flags", "not-a-url"])
def test_non_tls_non_local_urls_are_rejected(url):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, flagsmith_api_url=url)


def test_timeout_bounds_are_validated():
    with pytest.raises(ValidationError):
        Settings(_env_file=None, flagsmith_timeout_seconds=0)


def test_database_url_is_optional_and_secret_backed():
    from pydantic import SecretStr

    settings = Settings(_env_file=None, database_url="postgresql+psycopg://local-user:local-pass@localhost:5432/experimentops")
    assert isinstance(settings.database_url, SecretStr)
    assert "local-pass" not in str(settings.database_url)
    assert Settings(_env_file=None, database_url="").database_url is None
    assert Settings(_env_file=None).database_url is None


@pytest.mark.parametrize("url", [
    "sqlite:///experimentops.db",
    "postgresql://user:pass@localhost/db",
    "file:///tmp/database",
    "not-a-url",
    "postgresql+psycopg://localhost",
    "postgresql+psycopg://user:pass@localhost:99999/db",
])
def test_unsupported_or_malformed_database_urls_fail_without_echoing_value(url):
    with pytest.raises(ValueError) as caught:
        Settings(_env_file=None, database_url=url)
    assert url not in str(caught.value)


def test_database_url_uses_environment_variable_name(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://user:pass@localhost:5432/experimentops")
    settings = Settings(_env_file=None)
    assert settings.database_url is not None
    assert "**********" in str(settings.database_url)


def test_local_owner_subject_is_optional_and_normalized():
    assert Settings(_env_file=None).local_owner_subject is None
    assert Settings(
        _env_file=None,
        local_owner_subject="  local-operator  ",
    ).local_owner_subject == "local-operator"


@pytest.mark.parametrize("subject", ["", "   ", "invalid\nsubject", "x" * 256])
def test_invalid_local_owner_subject_is_rejected(subject):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, local_owner_subject=subject)
