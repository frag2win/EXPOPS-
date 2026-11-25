"""Optional live, read-only check for a dedicated non-production account.

Run with FLAGSMITH_INTEGRATION=1 and FLAGSMITH_API_KEY set. No writes occur.
"""

import os

import pytest

from app.core.config import Settings
from app.integrations.flagsmith.adapter import FlagsmithAdapter
from app.integrations.flagsmith.client import FlagsmithClient


pytestmark = pytest.mark.skipif(os.getenv("FLAGSMITH_INTEGRATION") != "1", reason="live Flagsmith integration is opt-in")


@pytest.mark.asyncio
async def test_live_credentials_can_list_accessible_projects():
    settings = Settings()
    assert settings.flagsmith_api_key, "Set FLAGSMITH_API_KEY for the opt-in live test"
    adapter = FlagsmithAdapter(FlagsmithClient(
        settings.flagsmith_api_url,
        settings.flagsmith_api_key.get_secret_value(),
        settings.flagsmith_timeout_seconds,
    ))
    assert await adapter.verify_authentication()
