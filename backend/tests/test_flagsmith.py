"""Mocked Flagsmith adapter, API, and security contract tests."""

import logging
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.integrations.flagsmith.adapter import FlagsmithAdapter
from app.integrations.flagsmith.client import FlagsmithClient
from app.integrations.flagsmith.exceptions import (
    FlagsmithAuthenticationError,
    FlagsmithInvalidResponseError,
    FlagsmithNotFoundError,
    FlagsmithPermissionError,
    FlagsmithRateLimitError,
    FlagsmithUnavailableError,
)
from app.main import create_app


def make_adapter(handler, api_key="unit-test-secret"):
    transport = httpx.MockTransport(handler)
    return FlagsmithAdapter(FlagsmithClient("https://api.flagsmith.com", api_key, 0.5, transport))


@pytest.mark.asyncio
async def test_successful_reads_use_verified_paths_and_auth_header():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/api/v1/projects/":
            return httpx.Response(200, json=[{"id": 12, "name": "Demo", "uuid": "abc"}])
        if request.url.path == "/api/v1/projects/12/environments/":
            return httpx.Response(200, json=[{"id": 3, "uuid": "env-uuid", "name": "Development", "api_key": "must-not-leak", "project": 12}])
        if request.url.path == "/api/v1/environments/get-by-uuid/env-uuid/":
            return httpx.Response(200, json={"id": 3, "uuid": "env-uuid", "name": "Development", "api_key": "must-not-leak", "project": 12, "description": "Test"})
        if request.url.path == "/api/v1/projects/12/features/":
            return httpx.Response(200, json={"count": 1, "next": None, "results": [{"id": 8, "name": "new_checkout", "description": "Checkout", "type": "STANDARD", "initial_value": "off"}]})
        if request.url.path == "/api/v1/projects/12/features/8/":
            return httpx.Response(200, json={"id": 8, "name": "new_checkout", "type": "STANDARD"})
        return httpx.Response(404)

    adapter = make_adapter(handler)
    assert await adapter.verify_authentication()
    assert (await adapter.list_projects())[0].id == "12"
    environment_summary = (await adapter.list_environments("12"))[0]
    environment_info = await adapter.get_environment("env-uuid")
    assert environment_summary.id == "env-uuid"
    assert environment_info.project_id == "12"
    assert "must-not-leak" not in str(environment_summary.model_dump())
    assert "must-not-leak" not in str(environment_info.model_dump())
    flags = await adapter.list_flags("env-uuid")
    assert flags[0].name == "new_checkout"
    assert (await adapter.get_flag("12", "8")).id == "8"
    assert "must-not-leak" not in str(flags)
    assert all(r.headers["Authorization"] == "Api-Key unit-test-secret" for r in calls)
    assert all("unit-test-secret" not in str(r.url) for r in calls)


@pytest.mark.parametrize(("status", "exception"), [
    (401, FlagsmithAuthenticationError), (403, FlagsmithPermissionError),
    (404, FlagsmithNotFoundError), (429, FlagsmithRateLimitError),
    (503, FlagsmithUnavailableError),
])
@pytest.mark.asyncio
async def test_upstream_error_mapping(status, exception):
    client = FlagsmithClient("https://api.flagsmith.com", "secret", transport=httpx.MockTransport(lambda _: httpx.Response(status, text="sensitive upstream body")))
    with pytest.raises(exception) as error:
        await client.get_json("/api/v1/projects/")
    assert "sensitive upstream body" not in str(error.value)


@pytest.mark.asyncio
async def test_missing_credential_fails_clearly():
    client = FlagsmithClient("https://api.flagsmith.com", None)
    with pytest.raises(Exception, match="credentials are not configured"):
        await client.get_json("/api/v1/projects/")


@pytest.mark.asyncio
async def test_invalid_upstream_shape_is_rejected():
    adapter = make_adapter(lambda _: httpx.Response(200, json={"unexpected": True}))
    with pytest.raises(FlagsmithInvalidResponseError):
        await adapter.list_projects()


@pytest.mark.asyncio
async def test_sanitized_live_environment_shape_maps_without_api_key():
    fixture_path = Path(__file__).parent / "fixtures" / "flagsmith_project_environments.sanitized.json"
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert isinstance(payload, list)
    assert "uuid" in payload[0] and "project" in payload[0]
    assert "api_key" not in payload[0]

    adapter = make_adapter(lambda _: httpx.Response(200, json=payload))
    environments = await adapter.list_environments("101")
    assert len(environments) == 1
    assert environments[0].id == "00000000-0000-4000-8000-000000000001"
    assert environments[0].name == "Development"
    assert environments[0].project_id == "101"


@pytest.mark.asyncio
async def test_timeout_is_sanitized():
    def timeout(_: httpx.Request):
        raise httpx.ReadTimeout("secret=do-not-leak")
    client = FlagsmithClient("https://api.flagsmith.com", "do-not-leak", transport=httpx.MockTransport(timeout))
    with pytest.raises(FlagsmithUnavailableError) as error:
        await client.get_json("/api/v1/projects/")
    assert "do-not-leak" not in str(error.value)


def test_api_missing_credential_and_health_endpoints():
    app = create_app(Settings(flagsmith_api_key=None))
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok", "service": "experimentops"}
        result = client.get("/api/v1/connections/flagsmith/status").json()
        assert result == {"configured": False, "authenticated": False, "message": "Flagsmith API credentials are not configured"}


def test_connection_status_reports_bad_credentials_without_leaking_upstream_detail():
    adapter = make_adapter(lambda _: httpx.Response(401, text="credential=private"))
    app = create_app(Settings(flagsmith_api_key="private"), adapter=adapter)
    with TestClient(app) as client:
        result = client.get("/api/v1/connections/flagsmith/status")
    assert result.status_code == 200
    assert result.json()["authenticated"] is False
    assert "private" not in result.text


def test_routes_map_authentication_errors_and_do_not_disclose_secrets(caplog):
    adapter = make_adapter(lambda _: httpx.Response(401, text="secret=upstream"), api_key="never-log-this")
    app = create_app(Settings(flagsmith_api_key="never-log-this"), adapter=adapter)
    with TestClient(app) as client, caplog.at_level(logging.WARNING):
        response = client.get("/api/v1/flagsmith/projects")
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "FLAGSMITH_AUTHENTICATION_FAILED"
    assert response.headers["X-Request-ID"] == response.json()["request_id"]
    assert "never-log-this" not in caplog.text
    assert "secret=upstream" not in response.text
