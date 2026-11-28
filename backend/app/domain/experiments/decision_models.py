"""Typed human decisions and their immutable review evidence."""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import Field, StrictStr, field_validator

from app.domain.experiments.models import (
    DomainModel,
    ExternalPrerequisite,
    PlanReadiness,
    ValidationCheck,
    ValidationMessage,
)


class DecisionType(StrEnum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class RevalidationOutcome(StrEnum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    NOT_RUN = "NOT_RUN"


class ApprovalRequest(DomainModel):
    """Empty approval body; identity and decision metadata are server-derived."""


class RejectionRequest(DomainModel):
    reason: StrictStr = Field(min_length=1, max_length=2000)

    @field_validator("reason")
    @classmethod
    def reason_must_be_meaningful(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("A rejection reason is required.")
        if len(normalized) > 2000:
            raise ValueError("A rejection reason must be at most 2000 characters.")
        return normalized


class DecisionRevalidationSnapshot(DomainModel):
    plan_id: UUID
    plan_version: int = Field(ge=1)
    content_hash: str = Field(min_length=64, max_length=64)
    revalidated_at: datetime | None = None
    outcome: RevalidationOutcome
    readiness: PlanReadiness | None = None
    summary: str
    checks: list[ValidationCheck] = Field(default_factory=list)
    errors: list[ValidationMessage] = Field(default_factory=list)
    warnings: list[ValidationMessage] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    external_prerequisites: list[ExternalPrerequisite] = Field(default_factory=list)


class DecisionAuditResponse(DomainModel):
    id: UUID
    event_type: StrictStr
    actor_subject: StrictStr
    occurred_at: datetime
    content_hash: StrictStr
    audit_hash: StrictStr
    revalidation_outcome: RevalidationOutcome
    idempotency_reference: StrictStr
    metadata: dict[str, Any]


class ExperimentPlanDecisionResponse(DomainModel):
    id: UUID
    plan_id: UUID
    plan_version: int = Field(ge=1)
    content_hash: StrictStr
    decision_type: DecisionType
    principal_subject: StrictStr
    decided_at: datetime
    rejection_reason: StrictStr | None = None
    revalidation: DecisionRevalidationSnapshot
    audit: DecisionAuditResponse


class DecisionHistoryResponse(DomainModel):
    plan_id: UUID
    items: list[ExperimentPlanDecisionResponse]
    page: int = Field(ge=1)
    page_size: int = Field(ge=1, le=100)
    total: int = Field(ge=0)
