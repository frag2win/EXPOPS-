"""API model for a persisted immutable experiment plan version."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import Field

from app.domain.experiments.draft_models import (
    ExperimentDraftResponse,
    FieldProvenance,
)
from app.domain.experiments.models import (
    DomainModel,
    ExperimentPlan,
    ExperimentSpecification,
    ValidationReport,
)


class PersistedExperimentPlan(DomainModel):
    id: UUID
    version: int = Field(ge=1)
    content_hash: str
    hash_format_version: int
    specification: ExperimentSpecification
    plan: ExperimentPlan
    validation: ValidationReport
    provenance: list[FieldProvenance]
    draft: ExperimentDraftResponse | None = None
    created_at: datetime


class ProvenanceSummary(DomainModel):
    ai_inferred: int = 0
    user_confirmed: int = 0
    user_provided: int = 0
    unknown: int = 0


class PlanListSummary(DomainModel):
    name: str | None = None
    hypothesis: str | None = None
    project: str | None = None
    environment: str | None = None
    feature_key: str | None = None
    feature_id: str | None = None
    readiness: str | None = None
    validation_summary: str | None = None


class PlanListItem(DomainModel):
    id: UUID
    current_version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime
    summary: PlanListSummary
    integrity_status: Literal["verified"] = "verified"


class PlanListResponse(DomainModel):
    items: list[PlanListItem]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)


class PlanReviewSummary(DomainModel):
    name: str | None = None
    hypothesis: str | None = None
    project: str | None = None
    environment: str | None = None
    feature_key: str | None = None
    feature_id: str | None = None
    variants: list[dict[str, Any]] = Field(default_factory=list)
    primary_metric: dict[str, Any] | None = None
    secondary_metrics: list[dict[str, Any]] = Field(default_factory=list)
    readiness: str | None = None
    validation_summary: str | None = None
    blocking_reasons: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    provenance: ProvenanceSummary


class PlanReviewResponse(DomainModel):
    id: UUID
    current_version: int = Field(ge=1)
    version: int = Field(ge=1)
    is_current: bool
    created_at: datetime
    version_created_at: datetime
    updated_at: datetime
    content_hash: str
    hash_format_version: int
    summary: PlanReviewSummary
    specification: dict[str, Any]
    plan: dict[str, Any]
    validation: dict[str, Any]
    provenance: list[dict[str, Any]]
    draft: ExperimentDraftResponse | None = None
    integrity_status: Literal["verified"] = "verified"


class PlanVersionHistoryItem(DomainModel):
    version: int = Field(ge=1)
    created_at: datetime
    readiness: str | None = None
    content_hash: str
    hash_format_version: int
    provenance: ProvenanceSummary
    integrity_status: Literal["verified"] = "verified"


class PlanVersionHistoryResponse(DomainModel):
    id: UUID
    current_version: int = Field(ge=1)
    items: list[PlanVersionHistoryItem]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)
