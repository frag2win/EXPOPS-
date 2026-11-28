"""Opt-in PostgreSQL integration checks for runtime and immutable plan persistence."""

import asyncio
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.api.dependencies import get_flagsmith_adapter
from app.core.config import Settings, get_settings
from app.llm.factory import get_structured_output_provider
from app.db.asyncio_runner import run_with_database_event_loop
from app.db.runtime import create_database_runtime, transaction_scope
from app.domain.experiments.draft_models import (
    ExperimentDraftResponse, FieldProvenance, GenerationMetadata, ProvenanceSource,
)
from app.domain.experiments.models import (
    ExperimentPlan, ExperimentPlanResponse, ExperimentSpecification,
    ExternalPrerequisite, PlanReadiness, ReadinessStatus, ValidationReport,
)
from app.domain.identity import Principal
from app.persistence.experiment_plans import (
    LEGACY_OWNER_SUBJECT, PlanIntegrityError, append_plan_version, create_plan,
    get_plan, get_plan_version, list_plans,
)
from app.main import create_app
from app.llm.protocols import GenerationUsage, StructuredGeneration


pytestmark = pytest.mark.skipif(
    os.getenv("EXPOPS_POSTGRES_INTEGRATION") != "1"
    or not os.getenv("TEST_DATABASE_URL"),
    reason="Set EXPOPS_POSTGRES_INTEGRATION=1 and TEST_DATABASE_URL to opt in",
)


def _principal(owner: str) -> Principal:
    return Principal(subject=owner)


def test_postgresql_connectivity_and_transaction_rollback():
    run_with_database_event_loop(_verify_postgresql_connectivity_and_transaction_rollback())


async def _verify_postgresql_connectivity_and_transaction_rollback():
    settings = Settings(_env_file=None, database_url=os.environ["TEST_DATABASE_URL"])
    runtime = create_database_runtime(settings.database_url)
    table_name = f"m41_rollback_{uuid4().hex}"
    try:
        async with runtime.session_factory() as session:
            async with session.begin():
                await session.execute(text("SELECT 1"))
            with pytest.raises(RuntimeError):
                async with session.begin():
                    await session.execute(text(f'CREATE TEMP TABLE "{table_name}" (id integer)'))
                    await session.execute(text(f'INSERT INTO "{table_name}" (id) VALUES (1)'))
                    raise RuntimeError("force rollback")
            result = await session.execute(
                text("SELECT to_regclass(:table_name)"),
                {"table_name": f"pg_temp.{table_name}"},
            )
            assert result.scalar_one() is None
    finally:
        await runtime.dispose()


def test_postgresql_immutable_plan_persistence_and_concurrent_versions(monkeypatch):
    database_url = os.environ["TEST_DATABASE_URL"]
    original_owner = os.getenv("LOCAL_OWNER_SUBJECT")
    owner = f"integration-owner-{uuid4()}"
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("LOCAL_OWNER_SUBJECT", owner)
    get_settings.cache_clear()
    backend_root = Path(__file__).resolve().parents[2]
    migration_config = Config(str(backend_root / "alembic.ini"))
    migration_config.set_main_option("script_location", str(backend_root / "migrations"))
    try:
        command.upgrade(migration_config, "m4_2_plans")
        legacy_id = uuid4()
        run_with_database_event_loop(
            _insert_legacy_plan(database_url, legacy_id),
        )
        command.upgrade(migration_config, "head")
        run_with_database_event_loop(
            _verify_legacy_migration(database_url, owner, legacy_id),
        )
        run_with_database_event_loop(_verify_postgresql_plan_persistence(database_url))
    finally:
        monkeypatch.setenv("DATABASE_URL", database_url)
        if original_owner is None:
            monkeypatch.delenv("LOCAL_OWNER_SUBJECT", raising=False)
        else:
            monkeypatch.setenv("LOCAL_OWNER_SUBJECT", original_owner)
        get_settings.cache_clear()


async def _insert_legacy_plan(database_url: str, plan_id):
    settings = Settings(_env_file=None, database_url=database_url)
    runtime = create_database_runtime(settings.database_url)
    created_at = datetime(2026, 1, 2, tzinfo=timezone.utc)
    updated_at = datetime(2026, 1, 3, tzinfo=timezone.utc)
    specification = {"name": "Legacy plan", "project": "project-name"}
    plan = {"summary": "Legacy snapshot", "resolved_project": {"id": "p-legacy", "name": "Project"}}
    validation = {"overall_status": "NEEDS_INPUT", "summary": "Legacy validation"}
    old_hash_payload = {
        "plan": plan,
        "validation": validation,
        "execution_enabled": False,
    }
    content_hash = hashlib.sha256(
        json.dumps(
            old_hash_payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    ).hexdigest()
    try:
        async with runtime.session_factory() as session:
            async with session.begin():
                await session.execute(
                    text(
                        """
                        INSERT INTO experiment_plans (
                            id, project_id, environment_id, version, status,
                            normalized_brief, plan_json, plan_hash, validation_json,
                            created_at, updated_at
                        ) VALUES (
                            :id, :project_id, :environment_id, :version, :status,
                            CAST(:specification AS jsonb), CAST(:plan AS jsonb),
                            :content_hash, CAST(:validation AS jsonb), :created_at, :updated_at
                        )
                        """
                    ),
                    {
                        "id": plan_id,
                        "project_id": "p-legacy",
                        "environment_id": "e-legacy",
                        "version": 7,
                        "status": "NEEDS_INPUT",
                        "specification": json.dumps(specification),
                        "plan": json.dumps(plan),
                        "content_hash": content_hash,
                        "validation": json.dumps(validation),
                        "created_at": created_at,
                        "updated_at": updated_at,
                    },
                )
    finally:
        await runtime.dispose()


async def _verify_legacy_migration(database_url: str, owner: str, plan_id):
    settings = Settings(_env_file=None, database_url=database_url)
    runtime = create_database_runtime(settings.database_url)
    try:
        async with runtime.session_factory() as session:
            async with session.begin():
                result = await get_plan(session, plan_id, _principal(owner))
                assert result is not None
                identity, version = result
                assert identity.owner_subject == LEGACY_OWNER_SUBJECT
                assert identity.current_version == 7
                assert version.version == 7
                assert version.legacy_project_id == "p-legacy"
                assert version.legacy_environment_id == "e-legacy"
                assert version.legacy_status == "NEEDS_INPUT"
                assert version.provenance_json == []
                assert version.hash_format_version == 0
                assert version.legacy_updated_at.year == 2026
                changed_principal = await get_plan(
                    session, plan_id, _principal("principal-changed-after-migration"),
                )
                assert changed_principal is not None
    finally:
        await runtime.dispose()


def _planning_snapshot(name: str) -> ExperimentPlanResponse:
    specification = ExperimentSpecification(
        name=name,
        project="project-id",
        environment="environment-id",
        hypothesis="A reviewed proposal",
    )
    plan = ExperimentPlan(summary="Persisted non-executing plan", specification=specification)
    validation = ValidationReport(
        overall_status=PlanReadiness.NEEDS_INPUT,
        summary="Readiness remains incomplete",
        specification=specification,
        external_prerequisites=[
            ExternalPrerequisite(
                name="project",
                status=ReadinessStatus.AVAILABLE,
                source="flagsmith",
                message="Verified project response.",
                evidence="project-id",
                independently_verified=True,
            )
        ],
    )
    return ExperimentPlanResponse(plan=plan, validation=validation)


async def _verify_postgresql_plan_persistence(database_url: str):
    settings = Settings(_env_file=None, database_url=database_url)
    runtime = create_database_runtime(settings.database_url)
    owner = f"integration-owner-{uuid4()}"
    provenance = [
        FieldProvenance(
            field_path="name",
            source=ProvenanceSource.USER_CONFIRMED,
            explanation="Confirmed by the test operator.",
        ),
        FieldProvenance(
            field_path="hypothesis",
            source=ProvenanceSource.AI_INFERRED,
            explanation="Suggestion awaits confirmation.",
        ),
        FieldProvenance(
            field_path="project",
            source=ProvenanceSource.USER_PROVIDED,
            explanation="Supplied in the original brief.",
        ),
        FieldProvenance(
            field_path="feature_key",
            source=ProvenanceSource.UNKNOWN,
            explanation="No value was supplied.",
        ),
    ]
    first_snapshot = _planning_snapshot("version one")
    draft = ExperimentDraftResponse(
        specification=first_snapshot.plan.specification,
        provenance=provenance,
        missing_information=[],
        clarification_questions=[],
        suggested_values=[],
        conflicts=[],
        ambiguities=[],
        planning=first_snapshot,
        generation=GenerationMetadata(provider="mock", model="mock", duration_ms=0),
    )
    try:
        async with transaction_scope(runtime.session_factory) as session:
            identity, first = await create_plan(
                session, _principal(owner), first_snapshot, provenance, draft,
            )

        async with runtime.session_factory() as session:
            async with session.begin():
                current = await get_plan(session, identity.id, _principal(owner))
                assert current is not None
                assert current[1].version == 1
                assert current[1].provenance_json == [p.model_dump(mode="json") for p in provenance]
                assert current[1].draft_json == draft.model_dump(mode="json")
                assert current[1].validation_json["external_prerequisites"][0]["evidence"] == "project-id"
                assert current[1].content_hash == first.content_hash
                assert await get_plan(
                    session, identity.id, _principal("different-principal"),
                ) is None
                assert await get_plan_version(
                    session, identity.id, 1, _principal(owner),
                ) is not None

        mutation_attempts = (
            (
                "UPDATE experiment_plan_versions SET content_hash = :value WHERE plan_id = :plan_id AND version = 1",
                {"value": "tampered", "plan_id": str(identity.id)},
            ),
            (
                "DELETE FROM experiment_plan_versions WHERE plan_id = :plan_id AND version = 1",
                {"plan_id": str(identity.id)},
            ),
        )
        for statement, parameters in mutation_attempts:
            with pytest.raises(DBAPIError):
                async with runtime.session_factory() as session:
                    async with session.begin():
                        await session.execute(text(statement), parameters)

        async with transaction_scope(runtime.session_factory) as session:
            second = await append_plan_version(
                session, identity.id, _principal(owner),
                _planning_snapshot("version two"), provenance,
            )
            assert second is not None and second[1].version == 2
        async with runtime.session_factory() as session:
            async with session.begin():
                old_version = await get_plan_version(
                    session, identity.id, 1, _principal(owner),
                )
                current = await get_plan(session, identity.id, _principal(owner))
                assert old_version is not None and old_version[1].content_hash == first.content_hash
                assert current is not None and current[1].version == 2

        async def append_concurrently(name: str) -> int:
            async with transaction_scope(runtime.session_factory) as session:
                result = await append_plan_version(
                    session, identity.id, _principal(owner),
                    _planning_snapshot(name), provenance,
                )
                assert result is not None
                return result[1].version

        allocated = await asyncio.gather(
            append_concurrently("concurrent-a"),
            append_concurrently("concurrent-b"),
        )
        assert sorted(allocated) == [3, 4]
        async with runtime.session_factory() as session:
            async with session.begin():
                current = await get_plan(session, identity.id, _principal(owner))
                assert current is not None and current[1].version == 4

        rolled_back_id = None
        with pytest.raises(RuntimeError):
            async with transaction_scope(runtime.session_factory) as session:
                rolled_back, _ = await create_plan(
                    session, _principal(owner),
                    _planning_snapshot("rolled back"), provenance,
                )
                rolled_back_id = rolled_back.id
                raise RuntimeError("test rollback")
        async with runtime.session_factory() as session:
            async with session.begin():
                assert await get_plan(
                    session, rolled_back_id, _principal(owner),
                ) is None

        await _verify_draft_api_round_trip(runtime, database_url, owner)
        await _verify_corruption_detection(runtime, owner)
    finally:
        await runtime.dispose()


async def _verify_corruption_detection(runtime, owner: str):
    async with transaction_scope(runtime.session_factory) as session:
        identity, _ = await create_plan(
            session, _principal(owner), _planning_snapshot("tamper detection"),
        )
    for sql in (
        """
        UPDATE experiment_plan_versions
        SET plan_json = jsonb_set(plan_json, '{summary}', '"tampered"'::jsonb)
        WHERE plan_id = :plan_id AND version = 1
        """,
        """
        UPDATE experiment_plan_versions
        SET hash_format_version = 999
        WHERE plan_id = :plan_id AND version = 1
        """,
    ):
        async with runtime.session_factory() as session:
            async with session.begin():
                await session.execute(text("ALTER TABLE experiment_plan_versions DISABLE TRIGGER experiment_plan_versions_immutable"))
                try:
                    await session.execute(text(sql), {"plan_id": identity.id})
                finally:
                    await session.execute(text("ALTER TABLE experiment_plan_versions ENABLE TRIGGER experiment_plan_versions_immutable"))
        with pytest.raises(PlanIntegrityError):
            async with runtime.session_factory() as session:
                async with session.begin():
                    await get_plan(session, identity.id, _principal(owner))
        with pytest.raises(PlanIntegrityError):
            async with runtime.session_factory() as session:
                async with session.begin():
                    await list_plans(
                        session, _principal(owner), offset=0, limit=20,
                    )


async def _verify_draft_api_round_trip(runtime, database_url: str, owner: str):
    class MockProvider:
        provider_name = "mock"
        model_name = "mock-gemini"

        async def generate(self, *, instructions, input_text, schema):
            return StructuredGeneration(
                output_json=json.dumps({
                    "specification": {"name": "Server-side proposal"},
                    "ambiguities": [],
                }),
                model=self.model_name,
                usage=GenerationUsage(input_tokens=2, output_tokens=3),
            )

    class ReadOnlyAdapter:
        client = type("Client", (), {"close": staticmethod(lambda: None)})()

        async def list_projects(self):
            raise AssertionError("This draft has no project selection.")

    settings = Settings(
        _env_file=None,
        database_url=database_url,
        local_owner_subject=owner,
    )
    app = create_app(settings, adapter=ReadOnlyAdapter())
    app.state.database_runtime = runtime
    app.dependency_overrides[get_structured_output_provider] = MockProvider
    app.dependency_overrides[get_flagsmith_adapter] = lambda: app.state.flagsmith_adapter
    prompt_marker = "PRIVATE_PROMPT_MUST_NOT_BE_STORED"
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://experimentops.test",
    ) as client:
        saved = await client.post(
            "/api/v1/experiments/drafts",
            json={
                "request": prompt_marker,
                "provided_specification": {"description": "Provided description"},
                "confirmed_specification": {"hypothesis": "Confirmed hypothesis"},
            },
        )
        assert saved.status_code == 201
        body = saved.json()
        persisted_provenance = [
            FieldProvenance.model_validate(item)
            for item in body["provenance"]
        ]
        async with runtime.session_factory() as session:
            async with session.begin():
                persisted = await get_plan(
                    session, UUID(body["id"]), _principal(owner),
                )
                assert persisted is not None
                assert persisted[0].owner_subject == owner
        assert body["draft"] is not None
        assert {item["source"] for item in body["provenance"]} >= {
            "AI_INFERRED", "USER_CONFIRMED", "USER_PROVIDED", "UNKNOWN",
        }
        source_by_path = {
            item["field_path"]: item["source"] for item in body["provenance"]
        }
        assert source_by_path["name"] == "AI_INFERRED"
        assert source_by_path["hypothesis"] == "USER_CONFIRMED"
        assert source_by_path["description"] == "USER_PROVIDED"
        assert source_by_path["instrumentation.sdk_readiness"] == "UNKNOWN"
        assert body["draft"]["specification"]["instrumentation"]["sdk_readiness"] == "unknown"
        assert all(
            item["status"] != "available"
            for item in body["validation"]["external_prerequisites"]
        )
        assert prompt_marker not in saved.text

        retrieved = await client.get(
            f"/api/v1/experiments/plans/{body['id']}/versions/{body['version']}",
        )
        assert retrieved.status_code == 200
        assert retrieved.json()["draft"] == body["draft"]
        assert retrieved.json()["provenance"] == body["provenance"]
        assert retrieved.json()["integrity_status"] == "verified"
        assert retrieved.json()["summary"]["provenance"]["ai_inferred"] >= 1

        async with transaction_scope(runtime.session_factory) as session:
            await append_plan_version(
                session,
                UUID(body["id"]),
                _principal(owner),
                _planning_snapshot("Persisted review version two"),
                persisted_provenance,
            )
            await create_plan(
                session,
                _principal(owner),
                _planning_snapshot("Another listed plan"),
                persisted_provenance,
            )
            foreign_identity, _ = await create_plan(
                session, _principal(f"{owner}-foreign"),
                _planning_snapshot("Foreign plan"),
            )

        history = await client.get(
            f"/api/v1/experiments/plans/{body['id']}/versions",
        )
        assert history.status_code == 200
        assert [item["version"] for item in history.json()["items"]] == [2, 1]
        assert all(
            item["integrity_status"] == "verified"
            for item in history.json()["items"]
        )
        current = await client.get(f"/api/v1/experiments/plans/{body['id']}")
        assert current.status_code == 200
        assert current.json()["version"] == 2
        assert current.json()["is_current"] is True
        assert current.json()["summary"]["readiness"] == "NEEDS_INPUT"
        assert current.json()["summary"]["provenance"]["unknown"] >= 1
        assert prompt_marker not in current.text

        first_page = await client.get("/api/v1/experiments/plans?page=1&page_size=1")
        first_page_repeat = await client.get(
            "/api/v1/experiments/plans?page=1&page_size=1",
        )
        pages = [
            await client.get(
                f"/api/v1/experiments/plans?page={page}&page_size=1",
            )
            for page in range(1, 5)
        ]
        assert first_page.status_code == first_page_repeat.status_code == 200
        assert all(page.status_code == 200 for page in pages)
        assert first_page.json()["total"] == 4
        first_id = first_page.json()["items"][0]["id"]
        assert first_id == first_page_repeat.json()["items"][0]["id"]
        listed_ids = [
            page.json()["items"][0]["id"]
            for page in pages
        ]
        assert len(set(listed_ids)) == 4
        assert str(foreign_identity.id) not in listed_ids
        assert all(
            "plan" not in item and "validation" not in item
            for page in pages
            for item in page.json()["items"]
        )
        assert (await client.get(
            "/api/v1/experiments/plans?owner_subject=another-principal",
        )).status_code == 422
        assert (await client.get(
            f"/api/v1/experiments/plans/{uuid4()}",
        )).json() == {"detail": "Experiment plan was not found."}
        assert (
            await client.get(
                f"/api/v1/experiments/plans/{foreign_identity.id}/versions",
            )
        ).status_code == 404
        assert (
            await client.get(
                f"/api/v1/experiments/plans/{foreign_identity.id}/versions/1",
            )
        ).status_code == 404
        async with transaction_scope(runtime.session_factory) as session:
            assert await append_plan_version(
                session,
                foreign_identity.id,
                _principal(owner),
                _planning_snapshot("Unauthorized version attempt"),
                persisted_provenance,
            ) is None

        settings.local_owner_subject = "different-configured-principal"
        hidden = await client.get(f"/api/v1/experiments/plans/{body['id']}")
        assert hidden.status_code == 404
        assert hidden.json() == {
            "detail": "Experiment plan was not found.",
        }

        async with runtime.session_factory() as session:
            async with session.begin():
                await session.execute(text(
                    "ALTER TABLE experiment_plan_versions "
                    "DISABLE TRIGGER experiment_plan_versions_immutable"
                ))
                try:
                    await session.execute(
                        text(
                            """
                            UPDATE experiment_plan_versions
                            SET plan_json = jsonb_set(plan_json, '{summary}', '"tampered"'::jsonb)
                            WHERE plan_id = :plan_id AND version = 2
                            """
                        ),
                        {"plan_id": body["id"]},
                    )
                finally:
                    await session.execute(text(
                        "ALTER TABLE experiment_plan_versions "
                        "ENABLE TRIGGER experiment_plan_versions_immutable"
                    ))
        settings.local_owner_subject = owner
        corrupted_review = await client.get(
            f"/api/v1/experiments/plans/{body['id']}",
        )
        assert corrupted_review.status_code == 500
        assert corrupted_review.json()["error"]["code"] == "PLAN_INTEGRITY_FAILED"
