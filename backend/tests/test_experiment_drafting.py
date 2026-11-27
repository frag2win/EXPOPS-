import asyncio
import json
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from google.genai import errors as genai_errors

from app.api.dependencies import get_flagsmith_adapter
from app.core.config import Settings
from app.domain.experiments.draft_models import (
    ExperimentDraftRequest, GeneratedDraftContent, ProvenanceSource,
)
from app.domain.experiments.models import ExperimentSpecification, PlanReadiness
from app.llm.errors import (
    LLMNotConfiguredError, LLMUnavailableError,
    GeminiAuthenticationError, GeminiBlockedResponseError, GeminiEmptyResponseError,
    GeminiInvalidJsonError, GeminiModelNotFoundError, GeminiOutputValidationError,
    GeminiRateLimitedError, GeminiRequestRejectedError, GeminiSchemaRejectedError,
    GeminiServiceUnavailableError, GeminiTimeoutError,
)
from app.llm.factory import get_structured_output_provider, configured_provider
from app.llm.protocols import GenerationUsage, StructuredGeneration
from app.llm.providers.gemini import GeminiProvider
from app.main import create_app

_SCHEMA = {"type": "object", "properties": {}, "required": []}


def generated_payload(**changes):
    payload = {
        "specification": {
            "name": "Simplified Checkout Experiment",
            "description": "Compare the existing and simplified checkout flows",
            "hypothesis": "A simplified checkout improves completed purchases",
            "project": None,
            "environment": None,
            "feature_key": None,
            "feature_id": None,
            "variants": [
                {"key": "control", "role": "control", "value": None, "allocation": None},
                {"key": "simplified", "role": "treatment", "value": None, "allocation": None},
            ],
            "primary_metric": {"name": "Successful purchases", "event_name": None, "aggregation": None, "direction": None},
            "secondary_metrics": [],
            "instrumentation": {
                "sdk_readiness": "unknown", "sdk_evidence": None,
                "exposure_identity_strategy": None, "conversion_identity_strategy": None,
                "exposure_event_requirement": None, "conversion_event_name": None,
            },
        },
        "ambiguities": [],
    }
    payload.update(changes)
    return payload


class FakeProvider:
    provider_name = "fake"
    model_name = "fake-model"

    def __init__(self, output=None, error=None):
        self.output = output if output is not None else generated_payload()
        self.error = error
        self.calls = []

    async def generate(self, *, instructions, input_text, schema):
        self.calls.append({"instructions": instructions, "input_text": input_text, "schema": schema})
        if self.error:
            raise self.error
        return StructuredGeneration(json.dumps(self.output), self.model_name, GenerationUsage(21, 13))


async def _close():
    pass


class NoReadFlagsmithAdapter:
    client = type("Client", (), {"close": staticmethod(_close)})()

    async def list_projects(self):
        raise AssertionError("No Flagsmith context was requested")

    async def close(self):
        pass


def draft_with_provider(provider, settings=None):
    app = create_app(settings or Settings(_env_file=None), adapter=NoReadFlagsmithAdapter())
    app.dependency_overrides[get_structured_output_provider] = lambda: provider
    app.dependency_overrides[get_flagsmith_adapter] = lambda: NoReadFlagsmithAdapter()
    return app


@pytest.mark.asyncio
async def test_natural_language_draft_is_structured_and_ai_suggestions_are_unconfirmed():
    provider = FakeProvider()
    from app.services.experiment_planning import ExperimentPlanningService
    from app.agents.experiment_specification import ExperimentSpecificationAgent

    response = await ExperimentSpecificationAgent(provider, ExperimentPlanningService(NoReadFlagsmithAdapter())).draft(
        ExperimentDraftRequest(request="I want to test a simplified checkout flow against the current checkout to improve conversion.")
    )
    assert response.specification.name == "Simplified Checkout Experiment"
    assert response.specification.feature_key is None
    assert response.specification.variants[0].allocation is None
    assert response.specification.primary_metric.event_name is None
    assert response.planning.validation.overall_status == PlanReadiness.NEEDS_INPUT
    assert any(p.field_path == "name" and p.source == ProvenanceSource.AI_INFERRED for p in response.provenance)
    assert any(s.field_path == "hypothesis" and s.requires_confirmation for s in response.suggested_values)
    assert all(s.requires_confirmation for s in response.suggested_values)
    assert any(p.field_path == "instrumentation.sdk_readiness" and p.source == ProvenanceSource.UNKNOWN for p in response.provenance)
    assert response.execution_performed is False
    assert response.generation.input_tokens == 21
    assert "Do not invent" in provider.calls[0]["instructions"]


@pytest.mark.asyncio
async def test_incomplete_and_ambiguous_draft_keeps_unknowns_and_asks_field_questions():
    output = generated_payload(
        specification={
            "name": None, "description": None, "hypothesis": None, "project": None,
            "environment": None, "feature_key": None, "feature_id": None, "variants": [],
            "primary_metric": None, "secondary_metrics": [],
            "instrumentation": {"sdk_readiness": "unknown", "sdk_evidence": None,
                "exposure_identity_strategy": None, "conversion_identity_strategy": None,
                "exposure_event_requirement": None, "conversion_event_name": None},
        },
        ambiguities=[{"field_path": "primary_metric.event_name", "reason": "The request describes conversion but names no event."}],
    )
    from app.services.experiment_planning import ExperimentPlanningService
    from app.agents.experiment_specification import ExperimentSpecificationAgent
    response = await ExperimentSpecificationAgent(FakeProvider(output), ExperimentPlanningService(NoReadFlagsmithAdapter())).draft(
        ExperimentDraftRequest(request="Test a new checkout.")
    )
    assert response.specification.primary_metric is None
    assert any("exact case-sensitive" in q for q in response.clarification_questions)
    assert any("warehouse readiness" in q.lower() for q in response.clarification_questions)
    assert response.planning.validation.overall_status == PlanReadiness.NEEDS_INPUT
    assert response.ambiguities[0].field_path == "primary_metric.event_name"


@pytest.mark.asyncio
async def test_user_confirmed_values_override_suggestions_and_are_not_reasked():
    output = generated_payload(ambiguities=[{"field_path": "name", "reason": "Name may need confirmation."}])
    from app.services.experiment_planning import ExperimentPlanningService
    from app.agents.experiment_specification import ExperimentSpecificationAgent
    request = ExperimentDraftRequest(
        request="Use the confirmed experiment name.",
        confirmed_specification=ExperimentSpecification(name="Owner approved name"),
    )
    response = await ExperimentSpecificationAgent(FakeProvider(output), ExperimentPlanningService(NoReadFlagsmithAdapter())).draft(request)
    assert response.specification.name == "Owner approved name"
    assert any(p.field_path == "name" and p.source == ProvenanceSource.USER_CONFIRMED for p in response.provenance)
    assert not any("clarify name" in q.lower() for q in response.clarification_questions)


@pytest.mark.asyncio
async def test_conflicting_structured_constraints_are_reported_and_confirmation_wins():
    from app.services.experiment_planning import ExperimentPlanningService
    from app.agents.experiment_specification import ExperimentSpecificationAgent
    request = ExperimentDraftRequest(
        request="Draft an experiment.",
        provided_specification=ExperimentSpecification(name="Provided name"),
        confirmed_specification=ExperimentSpecification(name="Confirmed name"),
    )
    response = await ExperimentSpecificationAgent(FakeProvider(), ExperimentPlanningService(NoReadFlagsmithAdapter())).draft(request)
    assert response.specification.name == "Confirmed name"
    assert response.conflicts[0].field_path == "name"
    assert any("conflicting values for name" in q.lower() for q in response.clarification_questions)


@pytest.mark.asyncio
async def test_explicit_invalid_allocation_still_fails_deterministic_validation():
    from app.services.experiment_planning import ExperimentPlanningService
    from app.agents.experiment_specification import ExperimentSpecificationAgent

    request = ExperimentDraftRequest(
        request="Use a control and treatment.",
        provided_specification=ExperimentSpecification.model_validate({
            "name": "Checkout test", "hypothesis": "Improve purchases",
            "variants": [
                {"key": "control", "role": "control", "value": False, "allocation": "70"},
                {"key": "variant", "role": "treatment", "value": True, "allocation": "20"},
            ],
        }),
    )
    response = await ExperimentSpecificationAgent(
        FakeProvider(), ExperimentPlanningService(NoReadFlagsmithAdapter())
    ).draft(request)
    assert response.planning.validation.overall_status == PlanReadiness.INVALID
    assert any(error.code == "ALLOCATION_TOTAL" for error in response.planning.validation.errors)


@pytest.mark.asyncio
async def test_exact_event_name_from_user_text_is_preserved_case_sensitively():
    output = generated_payload()
    output["specification"]["primary_metric"]["event_name"] = "Purchase_Completed"
    from app.services.experiment_planning import ExperimentPlanningService
    from app.agents.experiment_specification import ExperimentSpecificationAgent
    response = await ExperimentSpecificationAgent(FakeProvider(output), ExperimentPlanningService(NoReadFlagsmithAdapter())).draft(
        ExperimentDraftRequest(request="Use the primary metric event name `Purchase_Completed` exactly.")
    )
    assert response.specification.primary_metric.event_name == "Purchase_Completed"
    assert any(p.field_path == "primary_metric.event_name" and p.source == ProvenanceSource.USER_PROVIDED for p in response.provenance)


@pytest.mark.asyncio
async def test_unprovided_feature_allocation_and_metric_suggestions_are_withheld():
    output = generated_payload()
    output["specification"]["feature_key"] = "checkout_simplified"
    output["specification"]["variants"][0]["allocation"] = 70
    output["specification"]["variants"][1]["allocation"] = 30
    output["specification"]["primary_metric"]["event_name"] = "purchase_completed"
    from app.services.experiment_planning import ExperimentPlanningService
    from app.agents.experiment_specification import ExperimentSpecificationAgent
    response = await ExperimentSpecificationAgent(FakeProvider(output), ExperimentPlanningService(NoReadFlagsmithAdapter())).draft(
        ExperimentDraftRequest(request="Improve checkout conversion.")
    )
    assert response.specification.feature_key is None
    assert all(v.allocation is None for v in response.specification.variants)
    assert response.specification.primary_metric.event_name is None
    suggestions = {item.field_path for item in response.suggested_values}
    assert {"feature_key", "variants[0].allocation", "primary_metric.event_name"}.issubset(suggestions)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_output,error", [
    ("not-json", GeminiInvalidJsonError),
    ("{}", GeminiOutputValidationError),
    ('{"specification":{"name":7}}', GeminiOutputValidationError),
])
async def test_malformed_or_invalid_provider_output_is_rejected(bad_output, error):
    from app.services.experiment_planning import ExperimentPlanningService
    from app.agents.experiment_specification import ExperimentSpecificationAgent

    class RawProvider(FakeProvider):
        async def generate(self, *, instructions, input_text, schema):
            return StructuredGeneration(bad_output, "fake-model", GenerationUsage())

    with pytest.raises(error):
        await ExperimentSpecificationAgent(RawProvider(), ExperimentPlanningService(NoReadFlagsmithAdapter())).draft(
            ExperimentDraftRequest(request="Draft an experiment.")
        )


@pytest.mark.asyncio
async def test_json_allocation_string_is_not_silently_coerced():
    output = generated_payload()
    output["specification"]["variants"][0]["allocation"] = "70"
    from app.agents.experiment_specification import ExperimentSpecificationAgent
    from app.services.experiment_planning import ExperimentPlanningService
    with pytest.raises(GeminiOutputValidationError):
        await ExperimentSpecificationAgent(FakeProvider(output), ExperimentPlanningService(NoReadFlagsmithAdapter())).draft(
            ExperimentDraftRequest(request="Draft an experiment.")
        )


def test_provider_dto_accepts_omitted_optional_values_and_maps_them_to_unknowns():
    from app.agents.experiment_specification import _parse_generated, _provider_specification_to_domain
    content = _parse_generated('{"specification":{"name":"Checkout draft"}}')
    normalized = _provider_specification_to_domain(content.specification)
    specification = ExperimentSpecification.model_validate(normalized)
    assert specification.name == "Checkout draft"
    assert specification.variants is None
    assert specification.primary_metric is None
    assert specification.instrumentation.sdk_readiness.value == "unknown"
    assert specification.instrumentation.sdk_evidence is None


def test_explicit_null_nested_values_remain_null_and_readiness_stays_unknown():
    from app.agents.experiment_specification import _parse_generated, _provider_specification_to_domain
    raw = {
        "specification": {
            "name": None,
            "variants": [{"key": "control", "role": "control", "value": None, "allocation": None}],
            "primary_metric": None,
            "instrumentation": None,
        }
    }
    content = _parse_generated(json.dumps(raw))
    specification = ExperimentSpecification.model_validate(_provider_specification_to_domain(content.specification))
    assert specification.name is None
    assert specification.variants[0].allocation is None
    assert specification.primary_metric is None
    assert specification.instrumentation.sdk_readiness.value == "unknown"


def test_missing_provider_root_field_reports_redacted_pydantic_diagnostics():
    from app.agents.experiment_specification import _parse_generated
    with pytest.raises(GeminiOutputValidationError) as caught:
        _parse_generated('{"ambiguities":[]}')
    diag = caught.value.diagnostic["structural_errors"][0]
    assert diag["type"] == "top_level_contract_mismatch"
    assert diag["json_root_type"] == "object"
    assert diag["top_level_keys"] == ["ambiguities"]
    assert diag["missing_keys"] == ["specification"]
    assert diag["missing_required"] is True


def test_invalid_enum_diagnostic_reports_nested_path_without_value():
    from app.agents.experiment_specification import _parse_generated
    payload = generated_payload()
    payload["specification"]["variants"][0]["role"] = "PRIVATE_INVALID_ENUM_VALUE"
    with pytest.raises(GeminiOutputValidationError) as caught:
        _parse_generated(json.dumps(payload))
    diagnostic = caught.value.diagnostic
    issue = diagnostic["validation_errors"][0]
    assert issue["field_path"] == "specification.variants[0].role"
    assert issue["type"] in {"enum", "literal_error"}
    assert issue["actual_type"] == "string"
    assert "PRIVATE_INVALID_ENUM_VALUE" not in json.dumps(diagnostic)
    assert "PRIVATE_INVALID_ENUM_VALUE" not in str(caught.value)


def _parse_structure_error(value):
    from app.agents.experiment_specification import _parse_generated
    with pytest.raises(GeminiOutputValidationError) as caught:
        _parse_generated(value)
    diagnostics = caught.value.diagnostic
    return diagnostics.get("structural_errors", diagnostics.get("validation_errors", []))[0]


@pytest.mark.parametrize(("value", "root_type"), [
    ("[]", "array"),
    ('"a scalar root"', "string"),
])
def test_non_object_roots_report_safe_actual_root_type(value, root_type):
    diagnostic = _parse_structure_error(value)
    assert diagnostic["type"] == "root_type_mismatch"
    assert diagnostic["field_path"] == "<root>"
    assert diagnostic["json_root_type"] == root_type
    assert diagnostic["expected_type"] == "object"
    assert diagnostic["actual_type"] == root_type


def test_empty_object_reports_missing_specification_key():
    diagnostic = _parse_structure_error("{}")
    assert diagnostic["type"] == "top_level_contract_mismatch"
    assert diagnostic["top_level_keys"] == []
    assert diagnostic["missing_keys"] == ["specification"]


def test_unexpected_wrapper_is_not_silently_unwrapped_and_names_are_safe():
    wrapped = {"result": generated_payload()}
    diagnostic = _parse_structure_error(json.dumps(wrapped))
    assert diagnostic["missing_keys"] == ["specification"]
    assert diagnostic["unexpected_keys"] == ["result"]
    assert diagnostic["field_path"] == "<root>"


def test_unknown_extra_key_is_redacted_from_safe_structural_diagnostics():
    wrapped = generated_payload()
    wrapped["sensitive-value-key-that-must-not-be-logged"] = "never-copy-this-value"
    diagnostic = _parse_structure_error(json.dumps(wrapped))
    assert diagnostic["unexpected_keys"] == ["<unknown-key>"]
    assert "sensitive-value-key-that-must-not-be-logged" not in json.dumps(diagnostic)
    assert "never-copy-this-value" not in json.dumps(diagnostic)


def test_provenance_and_suggestions_are_application_outputs_not_provider_requirements():
    from app.agents.experiment_specification import _parse_generated
    payload = generated_payload()
    payload.pop("provenance", None)
    payload.pop("suggestions", None)
    assert _parse_generated(json.dumps(payload)).specification.name == "Simplified Checkout Experiment"
    payload["provenance"] = []
    diagnostic = _parse_structure_error(json.dumps(payload))
    assert diagnostic["unexpected_keys"] == ["provenance"]


def test_explicit_null_optional_nested_values_are_valid_but_null_variant_items_are_not():
    from app.agents.experiment_specification import _parse_generated
    optional_nulls = {"specification": {"variants": None, "primary_metric": None, "instrumentation": None}}
    parsed = _parse_generated(json.dumps(optional_nulls))
    assert parsed.specification.variants is None
    assert parsed.specification.primary_metric is None
    assert parsed.specification.instrumentation is None
    invalid = {"specification": {"variants": [None]}}
    diagnostic = _parse_structure_error(json.dumps(invalid))
    assert diagnostic["type"] == "nested_type_mismatch"
    assert diagnostic["field_path"] == "specification.variants[0]"
    assert diagnostic["expected_type"] == "object"
    assert diagnostic["actual_type"] == "null"


def test_invalid_variant_allocation_has_pydantic_field_diagnostic_not_generic_structure_error():
    from app.agents.experiment_specification import _parse_generated
    payload = generated_payload()
    payload["specification"]["variants"][0]["allocation"] = "do-not-disclose-this-value"
    with pytest.raises(GeminiOutputValidationError) as caught:
        _parse_generated(json.dumps(payload))
    issue = caught.value.diagnostic["validation_errors"][0]
    assert issue["field_path"] == "specification.variants[0].allocation"
    assert issue["actual_type"] == "string"
    assert issue["validation_category"] == "pydantic"
    assert "do-not-disclose-this-value" not in json.dumps(caught.value.diagnostic)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.parametrize(("path", "expected_category"), [
    ("unknown.provider.field", "unsupported_ambiguity_path"),
    ("", "unsupported_ambiguity_path"),
    ("primary_metric.unknown", "unsupported_ambiguity_path"),
    ("variants[0].unknown", "unsupported_ambiguity_path"),
])
def test_invalid_ambiguity_path_reports_allowlist_rule(path, expected_category):
    payload = generated_payload(ambiguities=[{"field_path": path, "reason": "Needs clarification."}])
    diagnostic = _parse_structure_error(json.dumps(payload))
    assert diagnostic["type"] == expected_category
    assert diagnostic["field_path"] == "ambiguities[0].field_path"


def test_prompt_schema_and_dto_agree_on_provider_owned_keys():
    from app.agents.experiment_specification import INSTRUCTIONS, _generation_schema
    from app.domain.experiments.ambiguity_registry import CANONICAL_AMBIGUITY_PATHS
    schema = _generation_schema()
    assert set(schema["properties"]) == {"specification", "ambiguities"}
    assert schema["required"] == ["specification"]
    assert "provenance" not in schema["properties"]
    assert "suggestions" not in schema["properties"]
    ambiguity_schema = schema["$defs"]["GeneratedAmbiguity"]["properties"]["field_path"]
    assert tuple(ambiguity_schema["enum"]) == CANONICAL_AMBIGUITY_PATHS
    assert all(f"`{path}`" in INSTRUCTIONS for path in CANONICAL_AMBIGUITY_PATHS)
    assert "Do not invent field names" in INSTRUCTIONS
    assert "omit that ambiguity" in INSTRUCTIONS


def test_ambiguity_registry_paths_resolve_to_actual_domain_model_fields():
    from typing import get_args, get_origin
    from app.domain.experiments.ambiguity_registry import AMBIGUITY_FIELDS
    from app.domain.experiments.models import (
        ExperimentSpecification, InstrumentationSpecification,
        MetricSpecification, VariantSpecification,
    )

    model_for_prefix = {
        "": ExperimentSpecification,
        "variants[]": VariantSpecification,
        "primary_metric": MetricSpecification,
        "secondary_metrics[]": MetricSpecification,
        "instrumentation": InstrumentationSpecification,
    }
    for entry in AMBIGUITY_FIELDS.values():
        path = entry.domain_field_path
        prefix, _, leaf = path.rpartition(".")
        model = model_for_prefix.get(prefix, ExperimentSpecification)
        if not leaf:
            assert path in model.model_fields
            continue
        assert leaf in model.model_fields, (entry.canonical_path, path)


@pytest.mark.parametrize(("canonical", "alias"), [
    ("variants[].allocation", "variants[0].allocation"),
    ("variants[].key", "variants[12].key"),
    ("secondary_metrics[].event_name", "secondary_metrics[3].event_name"),
])
def test_known_indexed_ambiguity_aliases_normalize_to_canonical_paths(canonical, alias):
    from app.domain.experiments.ambiguity_registry import canonicalize_ambiguity_path
    assert canonicalize_ambiguity_path(canonical) == canonical
    assert canonicalize_ambiguity_path(alias) == canonical


def test_every_registered_collection_path_accepts_only_its_indexed_alias():
    from app.domain.experiments.ambiguity_registry import CANONICAL_AMBIGUITY_PATHS, canonicalize_ambiguity_path
    collection_paths = [path for path in CANONICAL_AMBIGUITY_PATHS if "[]" in path]
    assert collection_paths
    for canonical in collection_paths:
        for index in (0, 4):
            alias = canonical.replace("[]", f"[{index}]", 1)
            assert canonicalize_ambiguity_path(alias) == canonical
    assert canonicalize_ambiguity_path("variants[0].made_up") is None
    assert canonicalize_ambiguity_path("variants[-1].allocation") is None
    assert canonicalize_ambiguity_path("variants[].allocation.extra") is None


def test_alias_is_normalized_by_production_parser_and_duplicate_alias_is_rejected():
    from app.agents.experiment_specification import _parse_generated
    payload = generated_payload(ambiguities=[{"field_path": "variants[0].allocation", "reason": "Allocation needs confirmation."}])
    assert _parse_generated(json.dumps(payload)).ambiguities[0].field_path == "variants[].allocation"
    duplicate = generated_payload(ambiguities=[
        {"field_path": "variants[].allocation", "reason": "First."},
        {"field_path": "variants[0].allocation", "reason": "Second."},
    ])
    diagnostic = _parse_structure_error(json.dumps(duplicate))
    assert diagnostic["type"] == "duplicate_ambiguity_path"
    assert diagnostic["field_path"] == "ambiguities[1].field_path"


def test_ambiguity_can_be_omitted_and_canonical_path_generates_guided_clarification():
    from app.agents.experiment_specification import ExperimentSpecificationAgent, _parse_generated
    from app.services.experiment_planning import ExperimentPlanningService
    from app.domain.experiments.ambiguity_registry import AMBIGUITY_FIELDS

    assert _parse_generated(json.dumps(generated_payload())).ambiguities == []
    output = generated_payload(ambiguities=[{
        "field_path": "primary_metric.event_name",
        "reason": "The event name was not provided.",
    }])
    response = asyncio.run(ExperimentSpecificationAgent(
        FakeProvider(output), ExperimentPlanningService(NoReadFlagsmithAdapter())
    ).draft(ExperimentDraftRequest(request="Test checkout conversion.")))
    guidance = AMBIGUITY_FIELDS["primary_metric.event_name"].clarification_guidance
    assert any(question.startswith(guidance) for question in response.clarification_questions)
    assert response.ambiguities[0].field_path == "primary_metric.event_name"


@pytest.mark.parametrize(("payload", "path", "expected", "actual", "category"), [
    ({"specification": None}, "specification", "object", "null", "nested_type_mismatch"),
    ({"specification": {"variants": "not-an-array"}}, "specification.variants", "array or null", "string", "nested_type_mismatch"),
    ({"specification": {"variants": [False]}}, "specification.variants[0]", "object", "boolean", "nested_type_mismatch"),
    ({"specification": {"variants": [{"allocation": "bad-number"}]}}, "specification.variants[0].allocation", "declared field constraint", "string", "value_error"),
])
def test_nested_structural_and_pydantic_failures_report_specific_safe_condition(payload, path, expected, actual, category):
    diagnostic = _parse_structure_error(json.dumps(payload))
    assert diagnostic["field_path"] == path
    assert diagnostic.get("expected_type", diagnostic.get("expected")) == expected
    assert diagnostic["actual_type"] == actual
    assert diagnostic["type"] == category


@pytest.mark.parametrize(("ambiguity", "category", "path"), [
    ({"field_path": "unrecognized_field", "reason": "Needs review."}, "unsupported_ambiguity_path", "ambiguities[0].field_path"),
    ({"field_path": "name", "reason": "DO_NOT_LOG " * 30}, "invalid_ambiguity_reason", "ambiguities[0].reason"),
])
def test_custom_ambiguity_checks_report_specific_field_without_retaining_text(ambiguity, category, path):
    diagnostic = _parse_structure_error(json.dumps(generated_payload(ambiguities=[ambiguity])))
    assert diagnostic["type"] == category
    assert diagnostic["field_path"] == path
    assert ambiguity["field_path"] not in json.dumps(diagnostic) if category == "invalid_ambiguity_path" else True
    assert ambiguity["reason"] not in json.dumps(diagnostic)


def test_nested_extra_field_diagnostic_reports_only_allowlisted_key_names():
    payload = {"specification": {"variants": [{"secretish-key-name": "sensitive-value"}]}}
    diagnostic = _parse_structure_error(json.dumps(payload))
    assert diagnostic["type"] == "extra_forbidden"
    assert diagnostic["field_path"] == "specification.variants[0].<unknown-field>"
    assert diagnostic["unexpected_keys"] == ["<unknown-key>"]
    assert "secretish-key-name" not in json.dumps(diagnostic)
    assert "sensitive-value" not in json.dumps(diagnostic)


def test_structural_diagnostics_are_internal_and_public_api_remains_generic(capsys):
    app = draft_with_provider(FakeProvider(output={"result": generated_payload()}))
    with TestClient(app) as client:
        response = client.post("/api/v1/experiments/draft", json={"request": "synthetic"})
    logs = capsys.readouterr().err
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "GEMINI_OUTPUT_VALIDATION_FAILED"
    assert "structural_errors" not in response.text
    assert '"type": "top_level_contract_mismatch"' in logs
    assert '"missing_keys": ["specification"]' in logs
    assert '"unexpected_keys": ["result"]' in logs


def test_nested_numeric_variants_and_metric_map_without_numeric_coercion_loss():
    from app.agents.experiment_specification import _parse_generated, _provider_specification_to_domain
    payload = generated_payload()
    payload["specification"]["variants"][0].update({"value": False, "allocation": 70.5})
    payload["specification"]["variants"][1].update({"value": True, "allocation": 29.5})
    payload["specification"]["primary_metric"].update({"event_name": "purchase_completed"})
    content = _parse_generated(json.dumps(payload))
    specification = ExperimentSpecification.model_validate(_provider_specification_to_domain(content.specification))
    assert [item.allocation for item in specification.variants] == [Decimal("70.5"), Decimal("29.5")]
    assert [item.value for item in specification.variants] == [False, True]
    assert specification.primary_metric.event_name == "purchase_completed"


@pytest.mark.asyncio
async def test_gemini_mock_runs_complete_offline_draft_mapping_provenance_clarifications_and_planner():
    from app.services.experiment_planning import ExperimentPlanningService
    from app.agents.experiment_specification import ExperimentSpecificationAgent
    fake_payload = generated_payload(ambiguities=[{
        "field_path": "primary_metric.event_name",
        "reason": "No event name was provided.",
    }])
    client = FakeGeminiClient(_response(json.dumps(fake_payload)))
    provider, _ = _provider_for(client)
    adapter = NoReadFlagsmithAdapter()
    response = await ExperimentSpecificationAgent(provider, ExperimentPlanningService(adapter)).draft(
        ExperimentDraftRequest(request="Test a simplified checkout against the current checkout.")
    )
    assert response.execution_performed is False
    assert response.planning.validation.overall_status == PlanReadiness.NEEDS_INPUT
    assert response.specification.primary_metric.event_name is None
    assert all(variant.allocation is None for variant in response.specification.variants)
    assert response.specification.instrumentation.sdk_readiness.value == "unknown"
    assert response.specification.primary_metric.event_name is None
    assert response.ambiguities[0].field_path == "primary_metric.event_name"
    assert any("case-sensitive event name" in question for question in response.clarification_questions)
    assert next(p for p in response.planning.validation.external_prerequisites if p.name == "warehouse").status.value == "unknown"
    assert response.clarification_questions
    assert all(item.requires_confirmation for item in response.suggested_values)
    assert not hasattr(adapter, "write")
    assert client.models.call["model"] == "gemini-3.5-flash-lite"


def test_api_does_not_expose_detailed_pydantic_diagnostics(capsys):
    payload = generated_payload()
    payload["specification"]["variants"][0]["role"] = "PRIVATE_INVALID_ENUM_VALUE"
    app = draft_with_provider(FakeProvider(payload))
    with TestClient(app) as client:
        response = client.post("/api/v1/experiments/draft", json={"request": "synthetic"})
    logs = capsys.readouterr().err
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "GEMINI_OUTPUT_VALIDATION_FAILED"
    assert "validation_errors" not in response.text
    assert "PRIVATE_INVALID_ENUM_VALUE" not in response.text
    assert "PRIVATE_INVALID_ENUM_VALUE" not in logs
    assert '"field_path": "specification.variants[0].role"' in logs


class FakeGeminiModels:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.call = None

    async def generate_content(self, **kwargs):
        self.call = kwargs
        if self.error:
            raise self.error
        return self.response


class FakeGeminiClient:
    def __init__(self, response=None, error=None):
        self.models = FakeGeminiModels(response, error)
        self.aio = self
        self.async_closed = False
        self.sync_closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        self.async_closed = True

    def close(self):
        self.sync_closed = True


def _response(text='{"ok":true}', model="gemini-3.5-flash-lite", usage=(10, 4)):
    prompt_tokens, output_tokens = usage
    return SimpleNamespace(
        candidates=[SimpleNamespace(
            finish_reason="STOP",
            content=SimpleNamespace(parts=[SimpleNamespace(text=text, thought=False)]),
        )],
        prompt_feedback=None,
        model_version=model,
        usage_metadata=SimpleNamespace(
            prompt_token_count=prompt_tokens,
            candidates_token_count=output_tokens,
        ),
    )


def _provider_for(client, timeout=0.5):
    captured = {}

    def factory(**kwargs):
        captured.update(kwargs)
        return client

    provider = GeminiProvider("gemini-test-secret", "gemini-3.5-flash-lite", timeout, client_factory=factory)
    return provider, captured


@pytest.mark.asyncio
async def test_gemini_request_uses_configured_model_native_json_schema_and_closes_client():
    from app.agents.experiment_specification import _generation_schema

    structured_json = json.dumps(generated_payload())
    client = FakeGeminiClient(_response(structured_json))
    provider, constructed = _provider_for(client)
    schema = _generation_schema()
    result = await provider.generate(instructions="system instructions", input_text="private request", schema=schema)

    call = client.models.call
    assert call["model"] == "gemini-3.5-flash-lite"
    assert call["contents"] == "private request"
    assert call["config"].system_instruction == "system instructions"
    assert call["config"].response_mime_type == "application/json"
    from app.llm.providers.gemini import to_gemini_json_schema
    assert call["config"].response_json_schema == to_gemini_json_schema(schema)
    assert "$defs" not in call["config"].response_json_schema
    assert call["config"].temperature == 0
    assert constructed["api_key"] == "gemini-test-secret"
    assert constructed["http_options"].timeout == 500
    assert constructed["http_options"].retry_options.attempts == 1
    assert result.output_json == structured_json
    from app.agents.experiment_specification import _parse_generated
    assert _parse_generated(result.output_json).specification.name == "Simplified Checkout Experiment"
    assert result.model == "gemini-3.5-flash-lite"
    assert result.usage.input_tokens == 10
    assert result.usage.output_tokens == 4
    assert client.async_closed and client.sync_closed


@pytest.mark.parametrize("status,body,error,code", [
    (401, {"error": {"message": "sensitive provider response and gemini-test-secret"}}, GeminiAuthenticationError, "GEMINI_AUTHENTICATION_ERROR"),
    (403, {"error": {"message": "sensitive provider response and gemini-test-secret"}}, GeminiAuthenticationError, "GEMINI_AUTHENTICATION_ERROR"),
    (400, {"error": {"message": "response_json_schema contains invalid schema"}}, GeminiSchemaRejectedError, "GEMINI_SCHEMA_REJECTED"),
    (400, {"error": {"message": "sensitive bad request gemini-test-secret"}}, GeminiRequestRejectedError, "GEMINI_REQUEST_REJECTED"),
    (404, {"error": {"message": "sensitive provider response"}}, GeminiModelNotFoundError, "GEMINI_MODEL_NOT_FOUND"),
    (429, {"error": {"message": "sensitive provider response"}}, GeminiRateLimitedError, "GEMINI_RATE_LIMITED"),
    (500, {"error": {"message": "sensitive provider response"}}, GeminiServiceUnavailableError, "GEMINI_SERVICE_UNAVAILABLE"),
    (503, {"error": {"message": "sensitive provider response"}}, GeminiServiceUnavailableError, "GEMINI_SERVICE_UNAVAILABLE"),
    (504, {"error": {"message": "sensitive provider response"}}, GeminiTimeoutError, "GEMINI_TIMEOUT"),
])
@pytest.mark.asyncio
async def test_gemini_api_errors_are_normalized_without_response_or_key_leak(status, body, error, code):
    sdk_error = genai_errors.APIError(status, body)
    client = FakeGeminiClient(error=sdk_error)
    provider, _ = _provider_for(client)

    with pytest.raises(error) as caught:
        await provider.generate(instructions="private", input_text="private", schema=_SCHEMA)
    assert "sensitive provider response" not in str(caught.value)
    assert "gemini-test-secret" not in str(caught.value)
    assert caught.value.code == code
    assert caught.value.upstream_status_code == status
    assert "sensitive" not in str(caught.value.diagnostic)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert client.async_closed and client.sync_closed


@pytest.mark.asyncio
async def test_gemini_sdk_authentication_exception_is_classified():
    sdk_error = genai_errors.ClientError(401, {"error": {"message": "credential detail"}})
    provider, _ = _provider_for(FakeGeminiClient(error=sdk_error))
    with pytest.raises(GeminiAuthenticationError) as caught:
        await provider.generate(instructions="private", input_text="private", schema=_SCHEMA)
    assert caught.value.code == "GEMINI_AUTHENTICATION_ERROR"
    assert caught.value.upstream_status_code == 401
    assert "credential detail" not in str(caught.value)


@pytest.mark.parametrize("response", [None, _response("")])
@pytest.mark.asyncio
async def test_gemini_empty_response_is_rejected_safely(response):
    provider, _ = _provider_for(FakeGeminiClient(response))
    with pytest.raises(GeminiEmptyResponseError):
        await provider.generate(instructions="private", input_text="private", schema=_SCHEMA)


@pytest.mark.parametrize("response", [
    SimpleNamespace(prompt_feedback=SimpleNamespace(block_reason="SAFETY"), candidates=[]),
    SimpleNamespace(prompt_feedback=None, candidates=[SimpleNamespace(finish_reason="SAFETY", content=None)]),
])
@pytest.mark.asyncio
async def test_gemini_blocked_responses_are_classified(response):
    provider, _ = _provider_for(FakeGeminiClient(response))
    with pytest.raises(GeminiBlockedResponseError):
        await provider.generate(instructions="private", input_text="private", schema=_SCHEMA)


@pytest.mark.parametrize("response", [
    SimpleNamespace(prompt_feedback=None, candidates=[]),
    SimpleNamespace(prompt_feedback=None, candidates=[SimpleNamespace(finish_reason="STOP", content=None)]),
    SimpleNamespace(prompt_feedback=None, candidates=[SimpleNamespace(finish_reason="STOP", content=SimpleNamespace(parts=[]))]),
])
@pytest.mark.asyncio
async def test_gemini_missing_candidate_or_text_is_classified_as_empty(response):
    provider, _ = _provider_for(FakeGeminiClient(response))
    with pytest.raises(GeminiEmptyResponseError):
        await provider.generate(instructions="private", input_text="private", schema=_SCHEMA)


@pytest.mark.asyncio
async def test_gemini_malformed_json_is_rejected_by_existing_pydantic_parser():
    from app.agents.experiment_specification import _parse_generated

    provider, _ = _provider_for(FakeGeminiClient(_response("not-json")))
    output = await provider.generate(instructions="private", input_text="private", schema=_SCHEMA)
    with pytest.raises(GeminiInvalidJsonError):
        _parse_generated(output.output_json)


def test_invalid_pydantic_fields_are_reported_separately_from_json_parsing():
    from app.agents.experiment_specification import _parse_generated
    with pytest.raises(GeminiOutputValidationError):
        _parse_generated('{"specification":{"name":7},"ambiguities":[]}')


def test_gemini_transport_schema_removes_unsupported_const_without_relaxing_domain_validation():
    from app.agents.experiment_specification import _generation_schema
    from app.llm.providers.gemini import to_gemini_json_schema
    original = _generation_schema()
    provider_schema = to_gemini_json_schema(original)

    def walk(node):
        if isinstance(node, dict):
            assert "$ref" not in node and "$defs" not in node and "const" not in node
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)
    walk(provider_schema)
    def find_properties(node, field):
        if isinstance(node, dict):
            props = node.get("properties")
            if isinstance(props, dict) and field in props:
                return props
            for value in node.values():
                found = find_properties(value, field)
                if found is not None:
                    return found
        elif isinstance(node, list):
            for value in node:
                found = find_properties(value, field)
                if found is not None:
                    return found
        return None
    instrumentation = find_properties(provider_schema, "sdk_readiness")
    assert instrumentation["sdk_readiness"]["enum"] == ["unknown"]
    assert instrumentation["sdk_evidence"]["type"] == ["null"]
    assert GeneratedDraftContent.model_validate(generated_payload())


@pytest.mark.asyncio
async def test_gemini_timeout_is_bounded_and_normalized():
    client = FakeGeminiClient()

    async def delay(**kwargs):
        client.models.call = kwargs
        await asyncio.sleep(0.2)
        return _response()

    client.models.generate_content = delay
    provider, _ = _provider_for(client, timeout=0.02)
    with pytest.raises(GeminiTimeoutError):
        await provider.generate(instructions="private", input_text="private", schema=_SCHEMA)
    assert client.async_closed and client.sync_closed


@pytest.mark.asyncio
async def test_gemini_request_cancellation_propagates_and_closes_client():
    client = FakeGeminiClient()
    started = asyncio.Event()

    async def wait_for_cancel(**kwargs):
        client.models.call = kwargs
        started.set()
        await asyncio.sleep(5)

    client.models.generate_content = wait_for_cancel
    provider, _ = _provider_for(client, timeout=10)
    task = asyncio.create_task(provider.generate(instructions="private", input_text="private", schema=_SCHEMA))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert client.async_closed and client.sync_closed


@pytest.mark.asyncio
async def test_gemini_unexpected_sdk_error_is_sanitized():
    client = FakeGeminiClient(error=RuntimeError("gemini-test-secret private prompt response"))
    provider, _ = _provider_for(client)
    with pytest.raises(GeminiServiceUnavailableError) as caught:
        await provider.generate(instructions="private", input_text="private", schema=_SCHEMA)
    assert "gemini-test-secret" not in str(caught.value)
    assert "private prompt" not in str(caught.value)


def test_gemini_provider_requires_a_real_configured_key_without_fallback():
    with pytest.raises(LLMNotConfiguredError):
        configured_provider(Settings(_env_file=None))

    configured = configured_provider(Settings(
        _env_file=None,
        gemini_api_key="test-gemini-key",
        gemini_model="gemini-3.5-flash-lite",
    ))
    assert isinstance(configured, GeminiProvider)
    assert configured.model_name == "gemini-3.5-flash-lite"


def test_gemini_settings_are_secret_and_use_requested_model_default():
    settings = Settings(_env_file=None, gemini_api_key="test-gemini-key")
    assert settings.gemini_model == "gemini-3.5-flash-lite"
    assert "**********" in str(settings.gemini_api_key)


def test_gemini_settings_load_expected_environment_variable_names(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini-key")
    monkeypatch.setenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
    settings = Settings(_env_file=None)
    assert settings.gemini_api_key.get_secret_value() == "test-gemini-key"
    assert settings.gemini_model == "gemini-3.5-flash-lite"


def test_api_without_provider_returns_safe_configuration_error():
    app = create_app(Settings(_env_file=None), adapter=NoReadFlagsmithAdapter())
    with TestClient(app) as client:
        response = client.post("/api/v1/experiments/draft", json={"request": "Draft an experiment."})
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "LLM_NOT_CONFIGURED"
    assert "Authorization" not in response.text


def test_api_draft_uses_fake_provider_and_never_calls_flagsmith_for_missing_project():
    provider = FakeProvider()
    app = draft_with_provider(provider)
    with TestClient(app) as client:
        response = client.post("/api/v1/experiments/draft", json={"request": "Test a simplified checkout."})
    assert response.status_code == 200
    assert response.json()["execution_performed"] is False
    assert response.json()["planning"]["validation"]["external_prerequisites"][-1]["status"] == "unknown"
    assert len(provider.calls) == 1


def test_api_rejects_oversized_draft_body():
    app = create_app(Settings(_env_file=None), adapter=NoReadFlagsmithAdapter())
    with TestClient(app) as client:
        response = client.post("/api/v1/experiments/draft", content=b"{" + b" " * 33_000 + b"}", headers={"content-type": "application/json"})
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "REQUEST_TOO_LARGE"


def test_provider_failure_does_not_log_or_return_prompt(capsys):
    prompt = "private-experiment-request-marker"
    app = draft_with_provider(FakeProvider(error=LLMUnavailableError()))
    with TestClient(app) as client:
        response = client.post("/api/v1/experiments/draft", json={"request": prompt})
    logs = capsys.readouterr().err
    assert response.status_code == 503
    assert prompt not in logs
    assert prompt not in response.text
    assert "Language model provider is unavailable" not in response.text
    assert "Authorization" not in response.text


def test_gemini_api_error_exposes_only_safe_category_and_upstream_status(capsys):
    failure = GeminiSchemaRejectedError(
        upstream_status_code=400,
        rpc_status="INVALID_ARGUMENT",
        reason="INVALID_ARGUMENT",
    )
    app = draft_with_provider(FakeProvider(error=failure))
    with TestClient(app) as client:
        response = client.post("/api/v1/experiments/draft", json={"request": "synthetic prompt marker"})
    logs = capsys.readouterr().err
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "GEMINI_SCHEMA_REJECTED"
    assert response.json()["error"]["upstream_status_code"] == 400
    assert "INVALID_ARGUMENT" not in response.text
    assert "synthetic prompt marker" not in response.text
    assert "synthetic prompt marker" not in logs
    assert '"upstream_status_code": 400' in logs
    assert '"category": "GEMINI_SCHEMA_REJECTED"' in logs


def test_generated_output_schema_is_closed_and_requires_only_provider_envelope():
    schema = GeneratedDraftContent.model_json_schema()
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["specification"]
    variants = schema["$defs"]["GeneratedVariant"]
    assert variants["additionalProperties"] is False
    assert not variants.get("required", [])
