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
