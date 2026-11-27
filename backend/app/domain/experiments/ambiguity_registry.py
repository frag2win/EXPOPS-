"""Canonical, domain-backed field paths accepted for AI ambiguity reports."""

from dataclasses import dataclass
from re import fullmatch
from types import MappingProxyType


@dataclass(frozen=True)
class AmbiguityField:
    canonical_path: str
    description: str
    domain_field_path: str
    clarification_guidance: str


_FIELDS = (
    AmbiguityField("name", "Experiment name", "name", "What experiment name should be used?"),
    AmbiguityField("description", "Experiment description", "description", "What should the experiment description say?"),
    AmbiguityField("hypothesis", "Experiment hypothesis", "hypothesis", "What hypothesis should the experiment evaluate?"),
    AmbiguityField("project", "Flagsmith project", "project", "Which exact Flagsmith project should be selected?"),
    AmbiguityField("environment", "Flagsmith environment", "environment", "Which exact Flagsmith environment should be selected?"),
    AmbiguityField("feature_key", "Flagsmith feature key", "feature_key", "What existing Flagsmith feature key should be used?"),
    AmbiguityField("feature_id", "Flagsmith feature identifier", "feature_id", "What existing Flagsmith feature identifier should be used?"),
    AmbiguityField("variants", "Variant collection", "variants", "Which control and treatment variants should be included?"),
    AmbiguityField("variants[]", "A variant item", "variants", "Which variant item needs clarification?"),
    AmbiguityField("variants[].key", "Variant key", "variants[].key", "What key should identify this variant?"),
    AmbiguityField("variants[].role", "Variant role", "variants[].role", "Which variant is control and which variants are treatments?"),
    AmbiguityField("variants[].value", "Variant value", "variants[].value", "What value should this variant use?"),
    AmbiguityField("variants[].allocation", "Variant allocation", "variants[].allocation", "What allocation should this variant receive?"),
    AmbiguityField("primary_metric", "Primary metric", "primary_metric", "What primary metric should be measured?"),
    AmbiguityField("primary_metric.name", "Primary metric name", "primary_metric.name", "What should the primary metric be called?"),
    AmbiguityField("primary_metric.event_name", "Primary metric event name", "primary_metric.event_name", "What exact, case-sensitive event name should be measured?"),
    AmbiguityField("primary_metric.aggregation", "Primary metric aggregation", "primary_metric.aggregation", "Which supported aggregation should be used?"),
    AmbiguityField("primary_metric.direction", "Primary metric direction", "primary_metric.direction", "Should the primary metric improve up or down?"),
    AmbiguityField("secondary_metrics", "Secondary metric collection", "secondary_metrics", "Which optional secondary metrics should be considered?"),
    AmbiguityField("secondary_metrics[]", "A secondary metric item", "secondary_metrics", "Which secondary metric item needs clarification?"),
    AmbiguityField("secondary_metrics[].name", "Secondary metric name", "secondary_metrics[].name", "What should this secondary metric be called?"),
    AmbiguityField("secondary_metrics[].event_name", "Secondary metric event name", "secondary_metrics[].event_name", "What exact, case-sensitive event name should this metric use?"),
    AmbiguityField("secondary_metrics[].aggregation", "Secondary metric aggregation", "secondary_metrics[].aggregation", "Which supported aggregation should this metric use?"),
    AmbiguityField("secondary_metrics[].direction", "Secondary metric direction", "secondary_metrics[].direction", "Should this secondary metric improve up or down?"),
    AmbiguityField("instrumentation", "Instrumentation details", "instrumentation", "Which instrumentation detail needs clarification?"),
    AmbiguityField("instrumentation.sdk_readiness", "SDK readiness", "instrumentation.sdk_readiness", "Is SDK instrumentation ready, and what evidence supports that status?"),
    AmbiguityField("instrumentation.sdk_evidence", "SDK readiness evidence", "instrumentation.sdk_evidence", "What evidence supports SDK instrumentation readiness?"),
    AmbiguityField("instrumentation.exposure_identity_strategy", "Exposure identity strategy", "instrumentation.exposure_identity_strategy", "Which stable identity is used for exposure events?"),
    AmbiguityField("instrumentation.conversion_identity_strategy", "Conversion identity strategy", "instrumentation.conversion_identity_strategy", "Which stable identity is used for conversion events?"),
    AmbiguityField("instrumentation.exposure_event_requirement", "Exposure event requirement", "instrumentation.exposure_event_requirement", "What exposure event must the application emit?"),
    AmbiguityField("instrumentation.conversion_event_name", "Conversion event name", "instrumentation.conversion_event_name", "What exact, case-sensitive conversion event name should the application emit?"),
)

AMBIGUITY_FIELDS = MappingProxyType({field.canonical_path: field for field in _FIELDS})
CANONICAL_AMBIGUITY_PATHS = tuple(AMBIGUITY_FIELDS)

_INDEXED_COLLECTION_ALIAS = r"(?P<collection>variants|secondary_metrics)\[(?P<index>0|[1-9][0-9]{0,8})\](?P<suffix>(?:\.[A-Za-z_][A-Za-z0-9_]*)*)"


def canonicalize_ambiguity_path(value: object) -> str | None:
    """Return a registered canonical path or None; normalize only known indexed aliases."""
    if not isinstance(value, str):
        return None
    if value in AMBIGUITY_FIELDS:
        return value
    match = fullmatch(_INDEXED_COLLECTION_ALIAS, value)
    if match is None:
        return None
    canonical = f"{match.group('collection')}[]{match.group('suffix')}"
    return canonical if canonical in AMBIGUITY_FIELDS else None
