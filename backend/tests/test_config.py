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
