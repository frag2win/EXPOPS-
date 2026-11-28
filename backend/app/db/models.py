"""Database models for immutable experiment plan snapshots."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.db.base_types import portable_json_type


class ExperimentPlanRecord(Base):
    """Stable identity and mutable pointer to the current immutable version."""

    __tablename__ = "experiment_plans"
    __table_args__ = (
        CheckConstraint("current_version >= 1", name="ck_experiment_plan_current_version"),
        ForeignKeyConstraint(
            ["id", "current_version"],
            ["experiment_plan_versions.plan_id", "experiment_plan_versions.version"],
            name="fk_experiment_plan_current_version",
            deferrable=True,
            initially="DEFERRED",
        ),
        Index("ix_experiment_plans_owner_subject", "owner_subject"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    owner_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ExperimentPlanVersionRecord(Base):
    """One append-only, integrity-checked plan snapshot."""

    __tablename__ = "experiment_plan_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["plan_id"],
            ["experiment_plans.id"],
            name="fk_experiment_plan_version_parent",
            ondelete="RESTRICT",
        ),
        CheckConstraint("version >= 1", name="ck_experiment_plan_version_positive"),
        CheckConstraint("hash_format_version >= 0", name="ck_experiment_plan_hash_format_nonnegative"),
        Index("ix_experiment_plan_versions_created_at", "created_at"),
        Index("ix_experiment_plan_versions_content_hash", "content_hash"),
    )

    plan_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    specification_json: Mapped[dict] = mapped_column(portable_json_type(), nullable=False)
    plan_json: Mapped[dict] = mapped_column(portable_json_type(), nullable=False)
    validation_json: Mapped[dict] = mapped_column(portable_json_type(), nullable=False)
    provenance_json: Mapped[list] = mapped_column(portable_json_type(), nullable=False)
    draft_json: Mapped[dict | None] = mapped_column(portable_json_type(), nullable=True)
    legacy_project_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    legacy_environment_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    legacy_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hash_format_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    legacy_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ExperimentPlanDecisionRecord(Base):
    """One immutable human decision for an exact persisted plan version."""

    __tablename__ = "experiment_plan_decisions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["plan_id", "plan_version"],
            ["experiment_plan_versions.plan_id", "experiment_plan_versions.version"],
            name="fk_experiment_plan_decision_version",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "plan_id",
            "plan_version",
            name="uq_experiment_plan_decision_version",
        ),
        UniqueConstraint(
            "id",
            "plan_id",
            "plan_version",
            name="uq_experiment_plan_decision_audit_target",
        ),
        UniqueConstraint(
            "principal_subject",
            "idempotency_key",
            name="uq_experiment_plan_decision_idempotency",
        ),
        CheckConstraint(
            "decision_type IN ('APPROVED', 'REJECTED')",
            name="ck_experiment_plan_decision_type",
        ),
        CheckConstraint(
            "(decision_type = 'APPROVED' AND rejection_reason IS NULL AND revalidation_outcome = 'PASSED') "
            "OR (decision_type = 'REJECTED' AND rejection_reason IS NOT NULL "
            "AND length(btrim(rejection_reason)) > 0 AND revalidation_outcome = 'NOT_RUN')",
            name="ck_experiment_plan_decision_payload",
        ),
        CheckConstraint("length(content_hash) = 64", name="ck_experiment_plan_decision_hash_length"),
        CheckConstraint("length(request_hash) = 64", name="ck_experiment_plan_decision_request_hash_length"),
        CheckConstraint(
            "length(btrim(idempotency_key)) BETWEEN 1 AND 128",
            name="ck_experiment_plan_decision_idempotency_key",
        ),
        Index(
            "ix_experiment_plan_decisions_plan_decided",
            "plan_id",
            "decided_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    plan_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    decision_type: Mapped[str] = mapped_column(String(16), nullable=False)
    principal_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    revalidation_outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    revalidation_json: Mapped[dict] = mapped_column(portable_json_type(), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class ExperimentPlanDecisionAuditRecord(Base):
    """Append-only audit event written atomically with a human decision."""

    __tablename__ = "experiment_plan_decision_audit"
    __table_args__ = (
        ForeignKeyConstraint(
            ["decision_id", "plan_id", "plan_version"],
            [
                "experiment_plan_decisions.id",
                "experiment_plan_decisions.plan_id",
                "experiment_plan_decisions.plan_version",
            ],
            name="fk_experiment_plan_decision_audit_decision",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["plan_id", "plan_version"],
            ["experiment_plan_versions.plan_id", "experiment_plan_versions.version"],
            name="fk_experiment_plan_decision_audit_version",
            ondelete="RESTRICT",
        ),
        UniqueConstraint("decision_id", name="uq_experiment_plan_decision_audit_decision"),
        CheckConstraint(
            "event_type = 'PLAN_DECISION_RECORDED'",
            name="ck_experiment_plan_decision_audit_event_type",
        ),
        CheckConstraint(
            "decision_type IN ('APPROVED', 'REJECTED')",
            name="ck_experiment_plan_decision_audit_decision_type",
        ),
        CheckConstraint(
            "revalidation_outcome IN ('PASSED', 'NOT_RUN')",
            name="ck_experiment_plan_decision_audit_revalidation_outcome",
        ),
        CheckConstraint("length(content_hash) = 64", name="ck_experiment_plan_decision_audit_hash_length"),
        CheckConstraint("length(audit_hash) = 64", name="ck_experiment_plan_decision_audit_integrity_hash_length"),
        CheckConstraint(
            "length(idempotency_reference) = 64",
            name="ck_experiment_plan_decision_audit_idempotency_reference_length",
        ),
        Index(
            "ix_experiment_plan_decision_audit_plan_occurred",
            "plan_id",
            "occurred_at",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    decision_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    plan_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    plan_version: Mapped[int] = mapped_column(Integer, nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    decision_type: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    audit_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    revalidation_outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    idempotency_reference: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_json: Mapped[dict] = mapped_column(portable_json_type(), nullable=False)
