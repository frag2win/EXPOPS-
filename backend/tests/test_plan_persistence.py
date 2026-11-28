import json
import hashlib
from contextlib import asynccontextmanager
from decimal import Decimal
from datetime import datetime, timezone
from enum import StrEnum
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from app.api import experiment_routes
from app.core.config import Settings
from app.domain.experiments.draft_models import ExperimentDraftRequest, ProvenanceSource
from app.domain.experiments.models import ExperimentSpecification
from app.main import create_app
from app.persistence.experiment_plans import (
    CURRENT_HASH_FORMAT_VERSION,
    LEGACY_HASH_FORMAT_VERSION,
    LEGACY_OWNER_SUBJECT,
    PlanIntegrityError,
    canonical_json,
    content_hash,
    get_plan,
    manual_provenance,
    verify_record_hash,
)


class _Direction(StrEnum):
    UP = "up"


def test_canonical_hash_ignores_dictionary_insertion_order():
    left = {"a": 1, "nested": {"x": 2, "y": 3}}
    right = {"nested": {"y": 3, "x": 2}, "a": 1}
    assert content_hash(left, {}, {}, []) == content_hash(right, {}, {}, [])


def test_canonical_json_handles_decimal_enum_unicode_null_and_nested_values():
    rendered = canonical_json({
        "enum": _Direction.UP,
        "decimal": Decimal("12.340"),
        "unicode": "café 雪",
        "null": None,
        "nested": [{"value": Decimal("0.25"), "direction": _Direction.UP}],
    })
    assert json.loads(rendered) == {
        "enum": "up",
        "decimal": "12.340",
        "unicode": "café 雪",
        "null": None,
        "nested": [{"value": "0.25", "direction": "up"}],
    }


def test_hash_verification_rejects_mutation_and_unknown_format():
    data = {
        "specification_json": {"name": "review"},
        "plan_json": {"summary": "plan"},
        "validation_json": {"checks": []},
        "provenance_json": [{"source": "USER_PROVIDED"}],
        "draft_json": None,
        "hash_format_version": CURRENT_HASH_FORMAT_VERSION,
    }
    data["content_hash"] = content_hash(
        data["specification_json"], data["plan_json"], data["validation_json"],
        data["provenance_json"], data["draft_json"],
    )
    record = SimpleNamespace(**data)
    verify_record_hash(record)
    record.plan_json["summary"] = "tampered"
    with pytest.raises(PlanIntegrityError):
        verify_record_hash(record)
    record.hash_format_version = 999
    with pytest.raises(PlanIntegrityError, match="Unsupported"):
        verify_record_hash(record)


def test_hash_covers_provenance_and_independently_verified_evidence():
    base = content_hash(
        {"name": "plan"},
        {"resolved_feature": {"id": "feature-1"}},
        {"evidence": "verified"},
        [{"source": "USER_PROVIDED"}],
    )
    assert base != content_hash(
        {"name": "plan"},
        {"resolved_feature": {"id": "feature-2"}},
        {"evidence": "verified"},
        [{"source": "USER_PROVIDED"}],
    )
    assert base != content_hash(
        {"name": "plan"},
        {"resolved_feature": {"id": "feature-1"}},
        {"evidence": "changed"},
        [{"source": "USER_PROVIDED"}],
    )
    assert base != content_hash(
        {"name": "plan"},
        {"resolved_feature": {"id": "feature-1"}},
        {"evidence": "verified"},
        [{"source": "USER_CONFIRMED"}],
    )


def test_draft_request_rejects_client_supplied_provenance():
    with pytest.raises(ValidationError):
        ExperimentDraftRequest.model_validate({
            "request": "Draft a plan",
            "provenance": [{"field_path": "name", "source": "USER_CONFIRMED"}],
        })


def test_legacy_hash_format_remains_verifiable_after_migration():
    plan = {"summary": "café", "specification": {"name": "雪"}}
    validation = {"summary": "validé"}
    legacy = json.dumps(
        {"plan": plan, "validation": validation, "execution_enabled": False},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    record = SimpleNamespace(
        plan_json=plan,
        validation_json=validation,
        hash_format_version=LEGACY_HASH_FORMAT_VERSION,
        content_hash=hashlib.sha256(legacy.encode("utf-8")).hexdigest(),
    )
    verify_record_hash(record)


def test_manual_plan_provenance_marks_only_submitted_values():
    specification = ExperimentSpecification.model_validate({
        "name": "Explicit name",
        "primary_metric": {"event_name": None},
    })
    provenance = {
        item.field_path: item.source
        for item in manual_provenance(specification)
    }
    assert provenance["name"] == ProvenanceSource.USER_PROVIDED
    assert provenance["primary_metric.event_name"] == ProvenanceSource.USER_PROVIDED
    assert provenance["hypothesis"] == ProvenanceSource.UNKNOWN


@pytest.mark.asyncio
async def test_migrated_legacy_owner_remains_visible_after_principal_change():
    plan_id = uuid4()
    snapshot = {
        "specification": {},
        "plan": {},
        "validation": {},
        "provenance": [],
        "draft": None,
    }
    version = SimpleNamespace(
        plan_id=plan_id,
        version=1,
        specification_json=snapshot["specification"],
        plan_json=snapshot["plan"],
        validation_json=snapshot["validation"],
        provenance_json=snapshot["provenance"],
        draft_json=snapshot["draft"],
        hash_format_version=CURRENT_HASH_FORMAT_VERSION,
        content_hash=content_hash(
            snapshot["specification"], snapshot["plan"],
            snapshot["validation"], snapshot["provenance"], snapshot["draft"],
        ),
    )
    identity = SimpleNamespace(
        id=plan_id,
        owner_subject=LEGACY_OWNER_SUBJECT,
        current_version=1,
    )

    class LegacySession:
        async def scalar(self, statement):
            bound_values = statement.compile().params.values()
            assert any(
                isinstance(value, (tuple, list))
                and "new-local-principal" in value
                and LEGACY_OWNER_SUBJECT in value
                for value in bound_values
            )
            return identity

        async def get(self, _model, key):
            assert key == (plan_id, 1)
            return version

    result = await get_plan(LegacySession(), plan_id, "new-local-principal")
    assert result is not None
    assert result[0].owner_subject == LEGACY_OWNER_SUBJECT


@pytest.mark.asyncio
async def test_server_side_draft_persistence_uses_agent_provenance_and_omits_prompt(monkeypatch):
    captured = {}

    class Provider:
        provider_name = "mock"
        model_name = "mock-model"

        async def generate(self, *, instructions, input_text, schema):
            from app.llm.protocols import GenerationUsage, StructuredGeneration

            captured["provider_input"] = input_text
            return StructuredGeneration(
                output_json=json.dumps({"specification": {"name": "AI proposed name"}}),
                model=self.model_name,
                usage=GenerationUsage(),
            )

    class Adapter:
        async def close(self):
            pass

        async def list_projects(self):
            raise AssertionError("A project was not supplied.")

    app = create_app(Settings(_env_file=None), adapter=Adapter())
    app.state.settings.local_owner_subject = "test-principal"
    app.state.database_runtime = SimpleNamespace(session_factory=object())

    @asynccontextmanager
    async def fake_transaction_scope(_factory):
        yield object()

    async def fake_create_plan(_session, owner, response, provenance, draft):
        captured["owner"] = owner
        captured["response"] = response
        captured["provenance"] = provenance
        captured["draft"] = draft
        version = SimpleNamespace(
            version=1,
            content_hash="a" * 64,
            hash_format_version=1,
            specification_json=response.plan.specification.model_dump(mode="json"),
            plan_json=response.plan.model_dump(mode="json"),
            validation_json=response.validation.model_dump(mode="json"),
            provenance_json=[item.model_dump(mode="json") for item in provenance],
            draft_json=draft.model_dump(mode="json"),
            created_at=datetime.now(timezone.utc),
        )
        return SimpleNamespace(id=uuid4()), version

    monkeypatch.setattr(experiment_routes, "transaction_scope", fake_transaction_scope)
    monkeypatch.setattr(experiment_routes, "create_plan", fake_create_plan)
    saved = await experiment_routes.draft_and_persist_experiment_plan(
        ExperimentDraftRequest(request="Private draft prompt"),
        SimpleNamespace(app=app),
        Provider(),
        Adapter(),
    )

    assert saved.version == 1
    assert saved.draft is not None
    assert any(item.source == ProvenanceSource.AI_INFERRED for item in saved.provenance)
    assert captured["owner"] == "test-principal"
    assert "Private draft prompt" not in json.dumps(captured["draft"].model_dump(mode="json"))


@pytest.mark.asyncio
async def test_storage_errors_are_sanitized():
    app = create_app(Settings(_env_file=None))
    request = SimpleNamespace(state=SimpleNamespace(request_id="request-1"))
    handler = app.exception_handlers[SQLAlchemyError]
    response = await handler(request, SQLAlchemyError("postgresql://secret"))
    assert response.status_code == 503
    assert b"postgresql://secret" not in response.body
    integrity_handler = app.exception_handlers[PlanIntegrityError]
    integrity_response = await integrity_handler(
        request, PlanIntegrityError("stored content and secret details"),
    )
    assert integrity_response.status_code == 500
    assert b"secret details" not in integrity_response.body
