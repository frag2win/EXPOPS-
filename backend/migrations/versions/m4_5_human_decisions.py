"""Persist immutable, auditable decisions for experiment plan versions.

Revision ID: m4_5_decisions
Revises: m4_2_immutable
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "m4_5_decisions"
down_revision: Union[str, None] = "m4_2_immutable"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "experiment_plan_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan_version", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("decision_type", sa.String(length=16), nullable=False),
        sa.Column("principal_subject", sa.String(length=255), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("revalidation_outcome", sa.String(length=16), nullable=False),
        sa.Column("revalidation_json", postgresql.JSONB(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=128), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "decision_type IN ('APPROVED', 'REJECTED')",
            name="ck_experiment_plan_decision_type",
        ),
        sa.CheckConstraint(
            "(decision_type = 'APPROVED' AND rejection_reason IS NULL AND revalidation_outcome = 'PASSED') "
            "OR (decision_type = 'REJECTED' AND rejection_reason IS NOT NULL "
            "AND length(btrim(rejection_reason)) > 0 AND revalidation_outcome = 'NOT_RUN')",
            name="ck_experiment_plan_decision_payload",
        ),
        sa.CheckConstraint(
            "length(content_hash) = 64",
            name="ck_experiment_plan_decision_hash_length",
        ),
        sa.CheckConstraint(
            "length(request_hash) = 64",
            name="ck_experiment_plan_decision_request_hash_length",
        ),
        sa.CheckConstraint(
            "length(btrim(idempotency_key)) BETWEEN 1 AND 128",
            name="ck_experiment_plan_decision_idempotency_key",
        ),
        sa.ForeignKeyConstraint(
            ["plan_id", "plan_version"],
            ["experiment_plan_versions.plan_id", "experiment_plan_versions.version"],
            name="fk_experiment_plan_decision_version",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "id",
            "plan_id",
            "plan_version",
            name="uq_experiment_plan_decision_audit_target",
        ),
        sa.UniqueConstraint(
            "plan_id",
            "plan_version",
            name="uq_experiment_plan_decision_version",
        ),
        sa.UniqueConstraint(
            "principal_subject",
            "idempotency_key",
            name="uq_experiment_plan_decision_idempotency",
        ),
    )
    op.create_index(
        "ix_experiment_plan_decisions_plan_decided",
        "experiment_plan_decisions",
        ["plan_id", "decided_at", "id"],
    )
    op.create_table(
        "experiment_plan_decision_audit",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("decision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan_version", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("decision_type", sa.String(length=16), nullable=False),
        sa.Column("actor_subject", sa.String(length=255), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("audit_hash", sa.String(length=64), nullable=False),
        sa.Column("revalidation_outcome", sa.String(length=16), nullable=False),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("idempotency_reference", sa.String(length=64), nullable=False),
        sa.Column("metadata_json", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint(
            "event_type = 'PLAN_DECISION_RECORDED'",
            name="ck_experiment_plan_decision_audit_event_type",
        ),
        sa.CheckConstraint(
            "decision_type IN ('APPROVED', 'REJECTED')",
            name="ck_experiment_plan_decision_audit_decision_type",
        ),
        sa.CheckConstraint(
            "revalidation_outcome IN ('PASSED', 'NOT_RUN')",
            name="ck_experiment_plan_decision_audit_revalidation_outcome",
        ),
        sa.CheckConstraint(
            "length(content_hash) = 64",
            name="ck_experiment_plan_decision_audit_hash_length",
        ),
        sa.CheckConstraint(
            "length(audit_hash) = 64",
            name="ck_experiment_plan_decision_audit_integrity_hash_length",
        ),
        sa.CheckConstraint(
            "length(idempotency_reference) = 64",
            name="ck_experiment_plan_decision_audit_idempotency_reference_length",
        ),
        sa.ForeignKeyConstraint(
            ["decision_id", "plan_id", "plan_version"],
            [
                "experiment_plan_decisions.id",
                "experiment_plan_decisions.plan_id",
                "experiment_plan_decisions.plan_version",
            ],
            name="fk_experiment_plan_decision_audit_decision",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["plan_id", "plan_version"],
            ["experiment_plan_versions.plan_id", "experiment_plan_versions.version"],
            name="fk_experiment_plan_decision_audit_version",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "decision_id",
            name="uq_experiment_plan_decision_audit_decision",
        ),
    )
    op.create_index(
        "ix_experiment_plan_decision_audit_plan_occurred",
        "experiment_plan_decision_audit",
        ["plan_id", "occurred_at", "id"],
    )
    op.execute(
        """
        CREATE FUNCTION reject_experiment_plan_decision_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'Experiment plan decisions and audit records are immutable'
                USING ERRCODE = '55000';
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER experiment_plan_decisions_immutable
        BEFORE UPDATE OR DELETE ON experiment_plan_decisions
        FOR EACH ROW EXECUTE FUNCTION reject_experiment_plan_decision_mutation();
        """
    )
    op.execute(
        """
        CREATE TRIGGER experiment_plan_decision_audit_immutable
        BEFORE UPDATE OR DELETE ON experiment_plan_decision_audit
        FOR EACH ROW EXECUTE FUNCTION reject_experiment_plan_decision_mutation();
        """
    )
    op.execute(
        """
        CREATE FUNCTION require_experiment_plan_decision_audit()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM experiment_plan_decision_audit
                WHERE decision_id = NEW.id
            ) THEN
                RAISE EXCEPTION 'Experiment plan decisions require an audit record'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NULL;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER experiment_plan_decision_audit_required
        AFTER INSERT ON experiment_plan_decisions
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW EXECUTE FUNCTION require_experiment_plan_decision_audit();
        """
    )


def downgrade() -> None:
    raise RuntimeError(
        "M4.5 decisions are immutable; downgrade would discard decision and audit records."
    )
