from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from app.api import experiment_routes
from app.core.config import Settings
from app.domain.experiments.decision_models import (
    DecisionAuditResponse,
    DecisionRevalidationSnapshot,
    DecisionType,
    ExperimentPlanDecisionResponse,
    RevalidationOutcome,
)
from app.domain.experiments.models import (
    ExperimentSpecification,
    ExternalPrerequisite,
    PlanReadiness,
    ReadinessStatus,
    ValidationReport,
)
from app.main import create_app
from app.persistence.experiment_decisions import build_revalidation_snapshot
from app.persistence.experiment_decisions import _revalidation_is_fresh


OWNER = "decision-test-owner"
PLAN_ID = uuid4()
VERSION = 3
CONTENT_HASH = "a" * 64
NOW = datetime(2026, 12, 1, tzinfo=timezone.utc)
SPECIFICATION = ExperimentSpecification(
    name="Checkout",
    hypothesis="Improve purchase completion.",
    project="project-id",
    environment="environment-id",
).model_dump(mode="json")


@asynccontextmanager
async def _fake_session_scope(_factory):
    yield object()


def _report(
    *,
    readiness: PlanReadiness = PlanReadiness.READY_FOR_REVIEW,
    warehouse: ReadinessStatus = ReadinessStatus.AVAILABLE,
) -> ValidationReport:
    prerequisites = [
        ExternalPrerequisite(
            name=name,
            status=warehouse if name == "warehouse" else ReadinessStatus.AVAILABLE,
            source="verified test fixture",
            message=f"{name} status",
            evidence=f"{name}-evidence",
            independently_verified=True,
        )
        for name in (
            "project",
            "environment",
            "feature",
            "feature_experiment_support",
            "warehouse",
        )
    ]
    return ValidationReport(
        overall_status=readiness,
        summary="Deterministic revalidation result.",
        specification=ExperimentSpecification.model_validate(SPECIFICATION),
        external_prerequisites=prerequisites,
    )


def _decision_response(
    decision_type: DecisionType,
    rejection_reason: str | None = None,
) -> ExperimentPlanDecisionResponse:
    outcome = (
        RevalidationOutcome.PASSED
        if decision_type == DecisionType.APPROVED
        else RevalidationOutcome.NOT_RUN
    )
    return ExperimentPlanDecisionResponse(
        id=uuid4(),
        plan_id=PLAN_ID,
        plan_version=VERSION,
        content_hash=CONTENT_HASH,
        decision_type=decision_type,
        principal_subject=OWNER,
        decided_at=NOW,
        rejection_reason=rejection_reason,
        revalidation=DecisionRevalidationSnapshot(
            plan_id=PLAN_ID,
            plan_version=VERSION,
            content_hash=CONTENT_HASH,
            revalidated_at=NOW if outcome == RevalidationOutcome.PASSED else None,
            outcome=outcome,
            readiness=(
                PlanReadiness.READY_FOR_REVIEW
                if outcome == RevalidationOutcome.PASSED
                else None
            ),
            summary="Decision recorded.",
        ),
        audit=DecisionAuditResponse(
            id=uuid4(),
            event_type="PLAN_DECISION_RECORDED",
            actor_subject=OWNER,
            occurred_at=NOW,
            content_hash=CONTENT_HASH,
            audit_hash="c" * 64,
            revalidation_outcome=outcome,
            idempotency_reference="b" * 64,
            metadata={"schema_version": 1},
        ),
    )


def _app(monkeypatch):
    app = create_app(Settings(_env_file=None, local_owner_subject=OWNER))
    app.state.database_runtime = SimpleNamespace(session_factory=object())
    monkeypatch.setattr(experiment_routes, "session_scope", _fake_session_scope)
    return app


@pytest.mark.asyncio
async def test_approval_revalidates_stored_version_before_atomic_record(monkeypatch):
    app = _app(monkeypatch)
    identity = SimpleNamespace(current_version=VERSION)
    version = SimpleNamespace(
        specification_json=SPECIFICATION,
        content_hash=CONTENT_HASH,
    )
    calls = {}

    async def get_owned(_session, plan_id, version_number, principal):
        assert (plan_id, version_number, principal.subject) == (
            PLAN_ID,
            VERSION,
            OWNER,
        )
        return identity, version

    async def no_existing(*_args):
        return None

    class PlanningService:
        def __init__(self, _adapter):
            pass

        async def plan(self, specification):
            assert specification.model_dump(mode="json") == SPECIFICATION
            return SimpleNamespace(validation=_report())

    async def record(_factory, **kwargs):
        calls.update(kwargs)
        return _decision_response(DecisionType.APPROVED), False

    monkeypatch.setattr(experiment_routes, "get_owned_plan_version", get_owned)
    monkeypatch.setattr(experiment_routes, "get_idempotent_decision", no_existing)
    monkeypatch.setattr(experiment_routes, "record_plan_decision", record)
    monkeypatch.setattr(experiment_routes, "ExperimentPlanningService", PlanningService)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://experimentops.test",
    ) as client:
        response = await client.post(
            f"/api/v1/experiments/plans/{PLAN_ID}/versions/{VERSION}/approve",
            json={},
            headers={"Idempotency-Key": "approve-1", "X-Owner-Subject": "attacker"},
        )

    assert response.status_code == 201, response.text
    assert response.json()["decision_type"] == "APPROVED"
    assert calls["principal"].subject == OWNER
    assert calls["content_hash"] == CONTENT_HASH
    assert calls["revalidation"].outcome == RevalidationOutcome.PASSED


@pytest.mark.asyncio
async def test_unknown_warehouse_readiness_blocks_approval_without_persisting(monkeypatch):
    app = _app(monkeypatch)
    identity = SimpleNamespace(current_version=VERSION)
    version = SimpleNamespace(
        specification_json=SPECIFICATION,
        content_hash=CONTENT_HASH,
    )

    async def get_owned(*_args):
        return identity, version

    async def no_existing(*_args):
        return None

    class PlanningService:
        def __init__(self, _adapter):
            pass

        async def plan(self, _specification):
            return SimpleNamespace(
                validation=_report(warehouse=ReadinessStatus.UNKNOWN),
            )

    async def must_not_record(*_args, **_kwargs):
        raise AssertionError("A blocked approval must not be persisted.")

    monkeypatch.setattr(experiment_routes, "get_owned_plan_version", get_owned)
    monkeypatch.setattr(experiment_routes, "get_idempotent_decision", no_existing)
    monkeypatch.setattr(experiment_routes, "record_plan_decision", must_not_record)
    monkeypatch.setattr(experiment_routes, "ExperimentPlanningService", PlanningService)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://experimentops.test",
    ) as client:
        response = await client.post(
            f"/api/v1/experiments/plans/{PLAN_ID}/versions/{VERSION}/approve",
            headers={"Idempotency-Key": "approve-unknown"},
        )

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "APPROVAL_REVALIDATION_FAILED"
    assert response.json()["detail"]["readiness"] == "READY_FOR_REVIEW"
    assert "warehouse status" in response.json()["detail"]["blocking_reasons"]


def test_revalidation_requires_complete_prerequisite_set():
    report = _report()
    report.external_prerequisites.pop()

    snapshot = build_revalidation_snapshot(
        PLAN_ID,
        VERSION,
        CONTENT_HASH,
        report,
    )

    assert snapshot.outcome == RevalidationOutcome.FAILED


def test_missing_or_stale_revalidation_evidence_blocks_approval():
    report = _report()
    report.external_prerequisites[0].evidence = None
    missing_evidence = build_revalidation_snapshot(
        PLAN_ID,
        VERSION,
        CONTENT_HASH,
        report,
    )
    assert missing_evidence.outcome == RevalidationOutcome.FAILED

    fresh = build_revalidation_snapshot(
        PLAN_ID,
        VERSION,
        CONTENT_HASH,
        _report(),
    )
    assert _revalidation_is_fresh(fresh, datetime.now(timezone.utc))
    fresh.revalidated_at = datetime.now(timezone.utc) - timedelta(seconds=61)
    assert not _revalidation_is_fresh(fresh, datetime.now(timezone.utc))


@pytest.mark.asyncio
async def test_rejection_uses_server_principal_and_normalized_reason(monkeypatch):
    app = _app(monkeypatch)
    identity = SimpleNamespace(current_version=VERSION)
    version = SimpleNamespace(
        specification_json=SPECIFICATION,
        content_hash=CONTENT_HASH,
    )
    captured = {}

    async def get_owned(*_args):
        return identity, version

    async def no_existing(*_args):
        return None

    async def record(_factory, **kwargs):
        captured.update(kwargs)
        return _decision_response(DecisionType.REJECTED, "Insufficient evidence"), False

    monkeypatch.setattr(experiment_routes, "get_owned_plan_version", get_owned)
    monkeypatch.setattr(experiment_routes, "get_idempotent_decision", no_existing)
    monkeypatch.setattr(experiment_routes, "record_plan_decision", record)

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://experimentops.test",
    ) as client:
        response = await client.post(
            f"/api/v1/experiments/plans/{PLAN_ID}/versions/{VERSION}/reject",
            json={"reason": "  Insufficient evidence  "},
            headers={"Idempotency-Key": "reject-1"},
        )

    assert response.status_code == 201
    assert captured["principal"].subject == OWNER
    assert captured["rejection_reason"] == "Insufficient evidence"
    assert captured["revalidation"].outcome == RevalidationOutcome.NOT_RUN


@pytest.mark.asyncio
async def test_decision_rejects_client_identity_and_hides_missing_or_foreign_plans(monkeypatch):
    app = _app(monkeypatch)

    async def hidden_plan(*_args):
        return None

    monkeypatch.setattr(experiment_routes, "get_owned_plan_version", hidden_plan)
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://experimentops.test",
    ) as client:
        body_override = await client.post(
            f"/api/v1/experiments/plans/{PLAN_ID}/versions/{VERSION}/reject",
            json={"reason": "Not ready", "principal_subject": "attacker"},
            headers={"Idempotency-Key": "reject-body"},
        )
        missing = await client.post(
            f"/api/v1/experiments/plans/{PLAN_ID}/versions/{VERSION}/reject",
            json={"reason": "Not ready"},
            headers={"Idempotency-Key": "reject-missing"},
        )
        hidden = await client.post(
            f"/api/v1/experiments/plans/{uuid4()}/versions/{VERSION}/reject",
            json={"reason": "Not ready"},
            headers={"Idempotency-Key": "reject-hidden"},
        )

    assert body_override.status_code == 422
    assert body_override.json()["error"]["code"] == "REQUEST_VALIDATION_FAILED"
    assert missing.status_code == hidden.status_code == 404
    assert missing.json() == hidden.json()
