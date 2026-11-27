"""Typed input, inspection and report models for experiment planning."""

from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, StrictStr


JsonScalar = StrictBool | StrictInt | StrictFloat | StrictStr


class DomainModel(BaseModel):
    """Reject unknown fields so planning input is never silently rewritten."""

    model_config = ConfigDict(extra="forbid")


class ReadinessValue(StrEnum):
    READY = "ready"
    NOT_READY = "not_ready"
    UNKNOWN = "unknown"


class VariantRole(StrEnum):
    CONTROL = "control"
    TREATMENT = "treatment"


class ReadinessStatus(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class PlanReadiness(StrEnum):
    INVALID = "INVALID"
    NEEDS_INPUT = "NEEDS_INPUT"
    BLOCKED = "BLOCKED"
    READY_FOR_REVIEW = "READY_FOR_REVIEW"


class CheckStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    UNKNOWN = "unknown"
    WARNING = "warning"


class MetricSpecification(DomainModel):
    """A metric definition supplied for review, not sent to Flagsmith."""

    name: StrictStr | None = None
    event_name: StrictStr | None = None
    aggregation: StrictStr | None = None
    direction: StrictStr | None = None


class VariantSpecification(DomainModel):
    """One desired experiment arm. Percentages remain Decimal for exact sums."""

    key: StrictStr | None = None
    role: VariantRole | None = None
    value: JsonScalar | None = None
    allocation: Decimal | None = None


class InstrumentationSpecification(DomainModel):
    """Operator-supplied instrumentation details; the service cannot inspect app code."""

    sdk_readiness: ReadinessValue = ReadinessValue.UNKNOWN
    sdk_evidence: StrictStr | None = None
    exposure_identity_strategy: StrictStr | None = None
    conversion_identity_strategy: StrictStr | None = None
    exposure_event_requirement: StrictStr | None = None
    conversion_event_name: StrictStr | None = None


class ExperimentSpecification(DomainModel):
    """Structured experiment intent; absent fields are preserved as missing input."""

    name: StrictStr | None = None
    description: StrictStr | None = None
    hypothesis: StrictStr | None = None
    project: StrictStr | None = None
    environment: StrictStr | None = None
    feature_key: StrictStr | None = None
    feature_id: StrictStr | None = None
    variants: list[VariantSpecification] | None = None
    primary_metric: MetricSpecification | None = None
    secondary_metrics: list[MetricSpecification] = Field(default_factory=list)
    instrumentation: InstrumentationSpecification = Field(default_factory=InstrumentationSpecification)


class ResolvedProject(DomainModel):
    id: str
    name: str


class ResolvedEnvironment(DomainModel):
    id: str
    name: str
    project_id: str | None = None


class ResolvedVariantOption(DomainModel):
    id: str
    key: str | None
    value_type: Literal["integer", "string", "boolean"]
    value: JsonScalar
    default_allocation: Decimal | None = None


class ResolvedFeature(DomainModel):
    id: str
    name: str
    feature_type: str | None = None
    initial_value: JsonScalar | None = None
    multivariate_options: list[ResolvedVariantOption] | None = None


class ExternalPrerequisite(DomainModel):
    name: str
    status: ReadinessStatus
    source: str
    message: str
    evidence: str | None = None
    independently_verified: bool = True


class PrerequisiteSnapshot(DomainModel):
    """Normalized read-only inspection result consumed by the pure validator."""

    project_status: ReadinessStatus = ReadinessStatus.UNKNOWN
    project_message: str = "Project has not been inspected."
    project: ResolvedProject | None = None
    environment_status: ReadinessStatus = ReadinessStatus.UNKNOWN
    environment_message: str = "Environment has not been inspected."
    environment: ResolvedEnvironment | None = None
    feature_status: ReadinessStatus = ReadinessStatus.UNKNOWN
    feature_message: str = "Feature has not been inspected."
    feature: ResolvedFeature | None = None
    feature_experiment_status: ReadinessStatus = ReadinessStatus.UNKNOWN
    feature_experiment_message: str = "Experiment support has not been established."
    warehouse_status: ReadinessStatus = ReadinessStatus.UNKNOWN
    warehouse_message: str = "Warehouse readiness has not been established."


class ValidationCheck(DomainModel):
    rule_id: str
    status: CheckStatus
    message: str
    field: str | None = None
    evidence: str | None = None


class ValidationMessage(DomainModel):
    code: str
    message: str
    field: str | None = None


class ValidationReport(DomainModel):
    overall_status: PlanReadiness
    summary: str
    specification: ExperimentSpecification
    resolved_project: ResolvedProject | None = None
    resolved_environment: ResolvedEnvironment | None = None
    resolved_feature: ResolvedFeature | None = None
    variant_allocation: list[VariantSpecification] = Field(default_factory=list)
    primary_metric: MetricSpecification | None = None
    checks: list[ValidationCheck] = Field(default_factory=list)
    errors: list[ValidationMessage] = Field(default_factory=list)
    warnings: list[ValidationMessage] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    external_prerequisites: list[ExternalPrerequisite] = Field(default_factory=list)
    recommended_next_steps: list[str] = Field(default_factory=list)


class ProposedStep(DomainModel):
    step_id: str
    kind: Literal["variant", "metric", "experiment"]
    summary: str
    state: Literal["proposed_not_executed"] = "proposed_not_executed"
    requires_future_approval: Literal[True] = True


class ExperimentPlan(DomainModel):
    summary: str
    specification: ExperimentSpecification
    resolved_project: ResolvedProject | None = None
    resolved_environment: ResolvedEnvironment | None = None
    resolved_feature: ResolvedFeature | None = None
    proposed_steps: list[ProposedStep] = Field(default_factory=list)


class ExperimentPlanResponse(DomainModel):
    plan: ExperimentPlan
    validation: ValidationReport
    execution_enabled: Literal[False] = False


class ExperimentEngineHealth(DomainModel):
    status: Literal["ok"] = "ok"
    engine: Literal["deterministic"] = "deterministic"
    write_operations_enabled: Literal[False] = False
