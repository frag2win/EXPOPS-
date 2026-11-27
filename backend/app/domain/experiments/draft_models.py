"""AI draft envelope kept separate from the authoritative experiment schema."""

from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import Field, StrictStr, field_validator

from app.domain.experiments.ambiguity_registry import CANONICAL_AMBIGUITY_PATHS
from app.domain.experiments.models import (
    DomainModel, ExperimentPlanResponse, ExperimentSpecification, JsonScalar,
    ReadinessValue, VariantRole,
)

CanonicalAmbiguityPath = Literal.__getitem__(CANONICAL_AMBIGUITY_PATHS)


class ProvenanceSource(StrEnum):
    USER_PROVIDED = "USER_PROVIDED"
    AI_INFERRED = "AI_INFERRED"
    USER_CONFIRMED = "USER_CONFIRMED"
    UNKNOWN = "UNKNOWN"


class GeneratedMetric(DomainModel):
    name: StrictStr | None = None
    event_name: StrictStr | None = None
    aggregation: StrictStr | None = None
    direction: StrictStr | None = None


class GeneratedVariant(DomainModel):
    key: StrictStr | None = None
    role: VariantRole | None = None
    value: JsonScalar | None = None
    allocation: Decimal | None = None

    @field_validator("allocation", mode="before")
    @classmethod
    def allocation_must_be_json_number(cls, value: object) -> object:
        if value is None or type(value) is int or isinstance(value, Decimal):
            return value
        raise ValueError("allocation must be a JSON number or null")


class GeneratedInstrumentation(DomainModel):
    sdk_readiness: Literal["unknown"] = "unknown"
    sdk_evidence: Literal[None] = None
    exposure_identity_strategy: Literal[None] = None
    conversion_identity_strategy: Literal[None] = None
    exposure_event_requirement: Literal[None] = None
    conversion_event_name: Literal[None] = None


class GeneratedSpecification(DomainModel):
    name: StrictStr | None = None
    description: StrictStr | None = None
    hypothesis: StrictStr | None = None
    project: StrictStr | None = None
    environment: StrictStr | None = None
    feature_key: StrictStr | None = None
    feature_id: StrictStr | None = None
    variants: list[GeneratedVariant] | None = None
    primary_metric: GeneratedMetric | None = None
    secondary_metrics: list[GeneratedMetric] = Field(default_factory=list)
    instrumentation: GeneratedInstrumentation | None = None


class GeneratedAmbiguity(DomainModel):
    field_path: CanonicalAmbiguityPath = Field(...)
    reason: StrictStr = Field(...)


class GeneratedDraftContent(DomainModel):
    specification: GeneratedSpecification = Field(...)
    ambiguities: list[GeneratedAmbiguity] = Field(default_factory=list)


class ExperimentDraftRequest(DomainModel):
    request: StrictStr = Field(min_length=1, max_length=8000)
    constraints: list[StrictStr] = Field(default_factory=list, max_length=20)
    provided_specification: ExperimentSpecification | None = None
    confirmed_specification: ExperimentSpecification | None = None

    @field_validator("request")
    @classmethod
    def request_must_not_be_whitespace(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("request cannot be blank")
        return value

    @field_validator("constraints")
    @classmethod
    def constraints_must_be_bounded(cls, values: list[str]) -> list[str]:
        if any(not item.strip() or len(item) > 500 for item in values):
            raise ValueError("each constraint must contain 1 to 500 characters")
        return values


class FieldProvenance(DomainModel):
    field_path: str
    source: ProvenanceSource
    explanation: str


class SuggestedValue(DomainModel):
    field_path: str
    value: JsonScalar | Decimal | None
    requires_confirmation: Literal[True] = True


class DraftConflict(DomainModel):
    field_path: str
    message: str
    clarification_question: str


class DetectedAmbiguity(DomainModel):
    field_path: str
    message: str


class GenerationMetadata(DomainModel):
    provider: str
    model: str
    duration_ms: int = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)


class ExperimentDraftResponse(DomainModel):
    specification: ExperimentSpecification
    provenance: list[FieldProvenance]
    missing_information: list[str]
    clarification_questions: list[str]
    suggested_values: list[SuggestedValue]
    conflicts: list[DraftConflict]
    ambiguities: list[DetectedAmbiguity]
    planning: ExperimentPlanResponse
    generation: GenerationMetadata
    execution_performed: Literal[False] = False
