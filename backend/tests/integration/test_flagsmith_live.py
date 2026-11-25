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
async def test_live_read_only_discovery_of_sandbox_resources():
    settings = Settings()
    assert settings.flagsmith_api_key, "Set FLAGSMITH_API_KEY for the opt-in live test"
    adapter = FlagsmithAdapter(FlagsmithClient(
        settings.flagsmith_api_url,
        settings.flagsmith_api_key.get_secret_value(),
        settings.flagsmith_timeout_seconds,
    ))
    assert await adapter.verify_authentication()
    projects = await adapter.list_projects()
    project = next((item for item in projects if item.name == "ExperimentOps Demo"), None)
    assert project is not None, "The configured account cannot see the expected sandbox project"

    # Capture the actual contract type without serializing the response. The
    # raw environment payload includes an environment api_key, so never log it.
    raw_environments = await adapter.client.get_json(
        f"/api/v1/projects/{adapter.client.path_segment(project.id)}/environments/"
    )
    raw_shape_is_list = isinstance(raw_environments, list) and bool(raw_environments)
    raw_item_is_object = raw_shape_is_list and isinstance(raw_environments[0], dict)
    raw_item_fields = set(raw_environments[0].keys()) if raw_item_is_object else set()
    del raw_environments  # Never retain or display the upstream environment api_key.
    assert raw_shape_is_list
    assert raw_item_is_object
    assert {"uuid", "name", "project"}.issubset(raw_item_fields)

    environments = await adapter.list_environments(project.id)
    environment = next((item for item in environments if item.name == "Development"), None)
    assert environment is not None
    detail = await adapter.get_environment(environment.id)
    assert detail.id == environment.id
    assert detail.project_id == project.id

    features = await adapter.list_flags(environment.id)
    assert any(item.name == "checkout_simplified" for item in features)
