from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import experiment_routes
from app.core.config import Settings
from app.main import create_app
from app.persistence.experiment_plans import PlanIntegrityError


OWNER = "review-test-owner"
PLAN_ID = uuid4()
CREATED_AT = datetime(2026, 11, 28, tzinfo=timezone.utc)


def _identity(current_version: int = 2):
    return SimpleNamespace(
        id=PLAN_ID,
        owner_subject=OWNER,
        current_version=current_version,
        created_at=CREATED_AT,
    )


def _version(number: int):
    return SimpleNamespace(
        plan_id=PLAN_ID,
        version=number,
        specification_json={
            "name": f"Plan version {number}",
            "hypothesis": "Review this hypothesis.",
            "project": "project-id",
            "environment": "environment-id",
            "feature_key": "checkout",
            "feature_id": "feature-id",
            "variants": [{"key": "control", "allocation": "50"}],
            "primary_metric": {"event_name": "purchase"},
            "secondary_metrics": [],
        },
        plan_json={"summary": "Persisted summary"},
        validation_json={
            "overall_status": "NEEDS_INPUT",
            "summary": "Readiness remains incomplete.",
            "errors": [{"message": "A metric event is unconfirmed."}],
            "checks": [{"status": "failed", "message": "Missing event evidence."}],
            "missing_information": ["event evidence"],
        },
        provenance_json=[
            {"field_path": "name", "source": "AI_INFERRED"},
            {"field_path": "hypothesis", "source": "USER_CONFIRMED"},
            {"field_path": "project", "source": "USER_PROVIDED"},
            {"field_path": "instrumentation.sdk_readiness", "source": "UNKNOWN"},
        ],
        draft_json=None,
        content_hash=f"{number:064x}",
        hash_format_version=1,
        created_at=CREATED_AT,
    )


@asynccontextmanager
async def _fake_session_scope(_factory):
    yield object()


def _app(monkeypatch):
    app = create_app(Settings(_env_file=None, local_owner_subject=OWNER))
    app.state.database_runtime = SimpleNamespace(session_factory=object())
    monkeypatch.setattr(experiment_routes, "session_scope", _fake_session_scope)
    return app


@pytest.mark.asyncio
async def test_list_uses_server_owner_and_database_pagination(monkeypatch):
    app = _app(monkeypatch)
    calls = []

    async def fake_list_plans(_session, owner, *, offset, limit):
        calls.append((owner, offset, limit))
        return [
            {
                "id": PLAN_ID,
                "current_version": 2,
                "created_at": CREATED_AT,
                "updated_at": CREATED_AT,
                "name": "Checkout test",
                "hypothesis": "Improve checkout completion.",
                "project": "project-id",
                "environment": "environment-id",
                "feature_key": "checkout",
                "feature_id": "feature-id",
                "readiness": "NEEDS_INPUT",
                "validation_summary": "Readiness remains incomplete.",
            }
        ], 3

    monkeypatch.setattr(experiment_routes, "list_plans", fake_list_plans)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://experimentops.test",
    ) as client:
        response = await client.get("/api/v1/experiments/plans?page=2&page_size=1")

    assert response.status_code == 200
    assert calls == [(OWNER, 1, 1)]
    body = response.json()
    assert (body["page"], body["page_size"], body["total"]) == (2, 1, 3)
    assert body["items"][0]["summary"]["readiness"] == "NEEDS_INPUT"
    assert body["items"][0]["integrity_status"] == "verified"


@pytest.mark.asyncio
async def test_list_rejects_owner_override_and_invalid_pagination(monkeypatch):
    app = _app(monkeypatch)

    async def unused_list(*_args, **_kwargs):
        raise AssertionError("Invalid requests must not reach persistence.")

    monkeypatch.setattr(experiment_routes, "list_plans", unused_list)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://experimentops.test",
    ) as client:
        owner_override = await client.get(
            "/api/v1/experiments/plans?owner_subject=another-principal",
        )
        invalid_page = await client.get("/api/v1/experiments/plans?page=0")
        excessive_page_size = await client.get(
            "/api/v1/experiments/plans?page_size=101",
        )

    assert owner_override.status_code == 422
    assert invalid_page.status_code == 422
    assert excessive_page_size.status_code == 422


@pytest.mark.asyncio
async def test_current_history_and_historical_review_responses(monkeypatch):
    app = _app(monkeypatch)
    identity = _identity()
    versions = [_version(2), _version(1)]
    versions_by_number = {version.version: version for version in versions}

    async def fake_get_plan(_session, plan_id, owner):
        assert plan_id == PLAN_ID
        assert owner == OWNER
        return identity, versions[0]

    async def fake_get_plan_version(_session, plan_id, version_number, owner):
        assert plan_id == PLAN_ID
        assert owner == OWNER
        return identity, versions_by_number[version_number]

    async def fake_list_versions(
        _session, plan_id, owner, *, offset, limit,
    ):
        assert (plan_id, owner, offset, limit) == (PLAN_ID, OWNER, 0, 20)
        return identity, versions, 2

    monkeypatch.setattr(experiment_routes, "get_plan", fake_get_plan)
    monkeypatch.setattr(experiment_routes, "get_plan_version", fake_get_plan_version)
    monkeypatch.setattr(experiment_routes, "list_plan_versions", fake_list_versions)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://experimentops.test",
    ) as client:
        current = await client.get(f"/api/v1/experiments/plans/{PLAN_ID}")
        history = await client.get(
            f"/api/v1/experiments/plans/{PLAN_ID}/versions",
        )
        historical = await client.get(
            f"/api/v1/experiments/plans/{PLAN_ID}/versions/1",
        )

    assert current.status_code == history.status_code == historical.status_code == 200
    current_body = current.json()
    assert current_body["is_current"] is True
    assert current_body["integrity_status"] == "verified"
    assert current_body["summary"]["readiness"] == "NEEDS_INPUT"
    assert current_body["summary"]["blocking_reasons"] == [
        "A metric event is unconfirmed.",
        "Missing event evidence.",
    ]
    assert current_body["summary"]["provenance"] == {
        "ai_inferred": 1,
        "user_confirmed": 1,
        "user_provided": 1,
        "unknown": 1,
    }
    history_body = history.json()
    assert [item["version"] for item in history_body["items"]] == [2, 1]
    assert all(item["integrity_status"] == "verified" for item in history_body["items"])
    historical_body = historical.json()
    assert historical_body["version"] == 1
    assert historical_body["is_current"] is False
    assert historical_body["integrity_status"] == "verified"


@pytest.mark.asyncio
async def test_missing_and_foreign_plans_return_same_safe_not_found(monkeypatch):
    app = _app(monkeypatch)

    async def hidden_plan(_session, _plan_id, owner):
        assert owner == OWNER
        return None

    monkeypatch.setattr(experiment_routes, "get_plan", hidden_plan)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://experimentops.test",
    ) as client:
        missing = await client.get(
            f"/api/v1/experiments/plans/{PLAN_ID}",
        )
        foreign = await client.get(
            f"/api/v1/experiments/plans/{uuid4()}",
        )

    assert missing.status_code == foreign.status_code == 404
    assert missing.json() == {"detail": "Experiment plan was not found."}
    assert foreign.json() == missing.json()


@pytest.mark.asyncio
async def test_integrity_failure_is_sanitized_for_review_endpoint(monkeypatch):
    app = _app(monkeypatch)

    async def corrupted_plan(*_args):
        raise PlanIntegrityError("tampered snapshot with private details")

    monkeypatch.setattr(experiment_routes, "get_plan", corrupted_plan)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://experimentops.test",
    ) as client:
        response = await client.get(f"/api/v1/experiments/plans/{PLAN_ID}")

    assert response.status_code == 500
    assert b"private details" not in response.content
    assert response.json()["error"]["code"] == "PLAN_INTEGRITY_FAILED"
