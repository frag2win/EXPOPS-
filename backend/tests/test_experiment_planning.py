from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.domain.experiments.models import (
    ExperimentSpecification, PlanReadiness, PrerequisiteSnapshot,
    ReadinessStatus, ResolvedEnvironment, ResolvedFeature, ResolvedProject,
    ResolvedVariantOption,
)
from app.domain.experiments.validator import validate_experiment
from app.integrations.flagsmith.exceptions import FlagsmithUnavailableError
from app.integrations.flagsmith.adapter import FlagsmithAdapter
from app.main import create_app
from app.services.experiment_planning import FlagsmithPrerequisiteInspector


def specification(**changes):
    data = {
        "name": "Checkout experiment", "description": "Compare checkout variants",
        "hypothesis": "Simpler checkout improves purchases", "project": "project",
        "environment": "environment", "feature_key": "checkout_simplified",
        "variants": [
            {"key": "control", "role": "control", "value": False, "allocation": "70"},
            {"key": "simplified", "role": "treatment", "value": True, "allocation": "30"},
        ],
        "primary_metric": {"name": "Purchases", "event_name": "purchase_completed", "aggregation": "occurrence", "direction": "up"},
        "instrumentation": {
            "sdk_readiness": "ready", "sdk_evidence": "Release checklist 12",
            "exposure_identity_strategy": "stable_user_id", "conversion_identity_strategy": "stable_user_id",
            "exposure_event_requirement": "Emit assignment exposure", "conversion_event_name": "purchase_completed",
        },
    }
    data.update(changes)
    return ExperimentSpecification.model_validate(data)


def ready_snapshot(**changes):
    options = [
        ResolvedVariantOption(id="1", key="control", value_type="boolean", value=False),
        ResolvedVariantOption(id="2", key="simplified", value_type="boolean", value=True),
    ]
    data = dict(
        project_status=ReadinessStatus.AVAILABLE, project_message="project found", project=ResolvedProject(id="1", name="project"),
        environment_status=ReadinessStatus.AVAILABLE, environment_message="environment verified", environment=ResolvedEnvironment(id="e1", name="environment", project_id="1"),
        feature_status=ReadinessStatus.AVAILABLE, feature_message="feature found", feature=ResolvedFeature(id="f1", name="checkout_simplified", feature_type="MULTIVARIATE", multivariate_options=options),
        feature_experiment_status=ReadinessStatus.AVAILABLE, feature_experiment_message="keyed options present",
        warehouse_status=ReadinessStatus.AVAILABLE, warehouse_message="warehouse connected in test evidence",
    )
    data.update(changes)
    return PrerequisiteSnapshot(**data)


def test_valid_70_30_plan_is_ready_for_review_only():
    report = validate_experiment(specification(), ready_snapshot())
    assert report.overall_status == PlanReadiness.READY_FOR_REVIEW
    assert sum((v.allocation for v in report.variant_allocation), Decimal(0)) == Decimal("100")
    assert report.errors == []


def test_multiple_treatments_are_supported_when_allocations_total_100():
    spec = specification(variants=[
        {"key": "control", "role": "control", "value": False, "allocation": "50"},
        {"key": "fast", "role": "treatment", "value": True, "allocation": "25"},
        {"key": "guided", "role": "treatment", "value": "guided", "allocation": "25"},
    ])
    snapshot = ready_snapshot(feature=ResolvedFeature(id="f1", name="checkout_simplified", feature_type="MULTIVARIATE", multivariate_options=[
        ResolvedVariantOption(id="1", key="control", value_type="boolean", value=False),
        ResolvedVariantOption(id="2", key="fast", value_type="boolean", value=True),
        ResolvedVariantOption(id="3", key="guided", value_type="string", value="guided"),
    ]))
    assert validate_experiment(spec, snapshot).overall_status == PlanReadiness.READY_FOR_REVIEW


@pytest.mark.parametrize("allocations", [("70", "20"), ("80", "30"), ("-10", "110")])
def test_invalid_allocations_are_not_normalized(allocations):
    spec = specification(variants=[
        {"key": "control", "role": "control", "value": False, "allocation": allocations[0]},
        {"key": "treatment", "role": "treatment", "value": True, "allocation": allocations[1]},
    ])
    assert validate_experiment(spec, ready_snapshot()).overall_status == PlanReadiness.INVALID


def test_duplicate_variant_keys_and_values_are_invalid():
    spec = specification(variants=[
        {"key": "same", "role": "control", "value": False, "allocation": 50},
        {"key": "same", "role": "treatment", "value": False, "allocation": 50},
    ])
    report = validate_experiment(spec, ready_snapshot())
    assert report.overall_status == PlanReadiness.INVALID
    assert {error.code for error in report.errors} >= {"VARIANT_KEYS_UNIQUE", "VARIANT_VALUES_DISTINCT"}


@pytest.mark.parametrize("variants", [
    [{"key": "t", "role": "treatment", "value": True, "allocation": 100}],
    [{"key": "c", "role": "control", "value": False, "allocation": 100}],
])
def test_control_or_treatment_required(variants):
    assert validate_experiment(specification(variants=variants), ready_snapshot()).overall_status == PlanReadiness.NEEDS_INPUT


def test_missing_metric_and_instrumentation_are_needs_input():
    spec = specification(primary_metric=None, instrumentation={})
    report = validate_experiment(spec, ready_snapshot())
    assert report.overall_status == PlanReadiness.NEEDS_INPUT
    assert "primary_metric" in report.missing_information


def test_empty_metric_event_is_invalid():
    spec = specification(primary_metric={"name": "Purchases", "event_name": "", "aggregation": "occurrence", "direction": "up"})
    assert any(e.code == "METRIC_EVENT_EMPTY" for e in validate_experiment(spec, ready_snapshot()).errors)


def test_metric_event_is_case_sensitive_and_duplicates_are_detected():
    spec = specification(
        primary_metric={"name": "Purchases", "event_name": "Purchase_Completed", "aggregation": "occurrence", "direction": "up"},
        secondary_metrics=[{"name": "Duplicate", "event_name": "purchase_completed", "aggregation": "occurrence", "direction": "up"}],
    )
    report = validate_experiment(spec, ready_snapshot())
    assert report.overall_status == PlanReadiness.INVALID
    assert any(e.code == "CONVERSION_EVENT_MISMATCH" for e in report.errors)
    assert any(e.code == "METRIC_DUPLICATE" for e in report.errors) is False


def test_duplicate_metric_definition_is_invalid():
    metric = {"name": "Purchases", "event_name": "purchase_completed", "aggregation": "occurrence", "direction": "up"}
    spec = specification(secondary_metrics=[metric])
    assert any(e.code == "METRIC_DUPLICATE" for e in validate_experiment(spec, ready_snapshot()).errors)


def test_unknown_warehouse_and_feature_support_require_input():
    snapshot = ready_snapshot(warehouse_status=ReadinessStatus.UNKNOWN, warehouse_message="no supported integration")
    report = validate_experiment(specification(), snapshot)
    assert report.overall_status == PlanReadiness.NEEDS_INPUT
    assert any(p.name == "warehouse" and p.status == ReadinessStatus.UNKNOWN for p in report.external_prerequisites)


def test_warehouse_confirmed_unavailable_blocks():
    snapshot = ready_snapshot(warehouse_status=ReadinessStatus.UNAVAILABLE, warehouse_message="connection check failed")
    assert validate_experiment(specification(), snapshot).overall_status == PlanReadiness.BLOCKED


def test_confirmed_prerequisite_unavailable_blocks_plan():
    snapshot = ready_snapshot(feature_status=ReadinessStatus.UNAVAILABLE, feature_message="feature absent", feature=None)
    assert validate_experiment(specification(), snapshot).overall_status == PlanReadiness.BLOCKED


def test_unknown_instrumentation_requires_input_and_not_ready_blocks():
    spec = specification(instrumentation={})
    assert validate_experiment(spec, ready_snapshot()).overall_status == PlanReadiness.NEEDS_INPUT
    assert validate_experiment(specification(instrumentation={"sdk_readiness": "not_ready"}), ready_snapshot()).overall_status == PlanReadiness.BLOCKED


class _FakeClient:
    async def close(self):
        pass


class _FakeAdapter:
    configured = True
    client = _FakeClient()

    async def list_projects(self):
        from app.integrations.flagsmith.schemas import Project
        return [Project(id="1", name="project")]

    async def list_environments(self, project_id):
        from app.integrations.flagsmith.schemas import Environment
        return [Environment(id="e1", name="environment", project_id=project_id)]

    async def get_environment(self, environment_id):
        from app.integrations.flagsmith.schemas import EnvironmentInfo
        return EnvironmentInfo(id="e1", name="environment", project_id="1")

    async def list_flags(self, environment_id):
        from app.integrations.flagsmith.schemas import FeatureFlag, FeatureVariantOption
        return [FeatureFlag(id="f1", name="checkout_simplified", type="MULTIVARIATE", multivariate_options=[
            FeatureVariantOption(id="1", key="control", value_type="boolean", value=False),
            FeatureVariantOption(id="2", key="simplified", value_type="boolean", value=True),
        ])]


async def test_prerequisite_inspector_reports_project_environment_and_feature_failures():
    from app.integrations.flagsmith.schemas import Environment, EnvironmentInfo

    class NoProjects(_FakeAdapter):
        async def list_projects(self):
            return []

    class NoEnvironments(_FakeAdapter):
        async def list_environments(self, project_id):
            return []

    class WrongProjectEnvironment(_FakeAdapter):
        async def get_environment(self, environment_id):
            return EnvironmentInfo(id="e1", name="environment", project_id="other")

    class NoFeature(_FakeAdapter):
        async def list_flags(self, environment_id):
            return []

    project_case = await FlagsmithPrerequisiteInspector(NoProjects()).inspect(specification())
    environment_case = await FlagsmithPrerequisiteInspector(NoEnvironments()).inspect(specification())
    ownership_case = await FlagsmithPrerequisiteInspector(WrongProjectEnvironment()).inspect(specification())
    feature_case = await FlagsmithPrerequisiteInspector(NoFeature()).inspect(specification())
    assert project_case.project_status == ReadinessStatus.UNAVAILABLE
    assert environment_case.environment_status == ReadinessStatus.UNAVAILABLE
    assert ownership_case.environment_status == ReadinessStatus.UNAVAILABLE
    assert feature_case.feature_status == ReadinessStatus.UNAVAILABLE


def test_api_plan_and_health_are_non_executing():
    with TestClient(create_app(adapter=_FakeAdapter())) as client:
        assert client.get("/api/v1/experiments/health").json()["write_operations_enabled"] is False
        response = client.post("/api/v1/experiments/plan", json=specification().model_dump(mode="json"))
    assert response.status_code == 200
    assert response.json()["execution_enabled"] is False


def test_api_validation_does_not_echo_invalid_input():
    with TestClient(create_app(adapter=_FakeAdapter())) as client:
        response = client.post("/api/v1/experiments/validate", json={"variants": [{"allocation": "DO_NOT_ECHO_SECRET"}]})
    assert response.status_code == 422
    assert "DO_NOT_ECHO_SECRET" not in response.text


class _FailingAdapter(_FakeAdapter):
    async def list_projects(self):
        raise FlagsmithUnavailableError("Flagsmith is temporarily unavailable")


def test_api_maps_upstream_failure_to_sanitized_error():
    with TestClient(create_app(adapter=_FailingAdapter())) as client:
        response = client.post("/api/v1/experiments/validate", json=specification().model_dump(mode="json"))
    assert response.status_code == 503
    assert "Authorization" not in response.text


def test_adapter_maps_flagsmith_multivariate_option_contract_strictly():
    feature = FlagsmithAdapter._feature({
        "id": 7, "name": "checkout", "type": "MULTIVARIATE", "multivariate_options": [
            {"id": 9, "key": "compact", "type": "bool", "boolean_value": True, "default_percentage_allocation": "30.00"},
        ],
    })
    assert feature.multivariate_options[0].value_type == "boolean"
    assert feature.multivariate_options[0].value is True
    assert feature.multivariate_options[0].default_allocation == Decimal("30.00")


def test_adapter_rejects_unknown_multivariate_option_shape():
    from app.integrations.flagsmith.exceptions import FlagsmithInvalidResponseError
    with pytest.raises(FlagsmithInvalidResponseError):
        FlagsmithAdapter._feature({"id": 7, "name": "checkout", "multivariate_options": [{"id": 1, "type": "mystery"}]})
