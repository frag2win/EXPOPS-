"""Split plan identities from immutable, provenance-preserving versions.

Revision ID: m4_2_immutable
Revises: m4_2_plans
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "m4_2_immutable"
down_revision: Union[str, None] = "m4_2_plans"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

LEGACY_TABLE = "experiment_plans_legacy_m4_2"


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1 FROM experiment_plans
                WHERE version < 1 OR id IS NULL OR plan_hash IS NULL
            ) THEN
                RAISE EXCEPTION 'Existing experiment plan data is invalid; refusing M4.2 migration';
            END IF;
        END
        $$;
        """
    )
    op.rename_table("experiment_plans", LEGACY_TABLE)
    op.create_table(
        "experiment_plans",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_subject", sa.String(length=255), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("current_version >= 1", name="ck_experiment_plan_current_version"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_experiment_plans_owner_subject",
        "experiment_plans",
        ["owner_subject"],
    )
    op.create_table(
        "experiment_plan_versions",
        sa.Column("plan_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("specification_json", postgresql.JSONB(), nullable=False),
        sa.Column("plan_json", postgresql.JSONB(), nullable=False),
        sa.Column("validation_json", postgresql.JSONB(), nullable=False),
        sa.Column("provenance_json", postgresql.JSONB(), nullable=False),
        sa.Column("draft_json", postgresql.JSONB(), nullable=True),
        sa.Column("legacy_project_id", sa.String(length=255), nullable=True),
        sa.Column("legacy_environment_id", sa.String(length=255), nullable=True),
        sa.Column("legacy_status", sa.String(length=32), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("hash_format_version", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("legacy_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("version >= 1", name="ck_experiment_plan_version_positive"),
        sa.CheckConstraint("hash_format_version >= 0", name="ck_experiment_plan_hash_format_nonnegative"),
        sa.ForeignKeyConstraint(
            ["plan_id"],
            ["experiment_plans.id"],
            name="fk_experiment_plan_version_parent",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("plan_id", "version"),
    )
    op.create_index(
        "ix_experiment_plan_versions_created_at",
        "experiment_plan_versions",
        ["created_at"],
    )
    op.create_index(
        "ix_experiment_plan_versions_content_hash",
        "experiment_plan_versions",
        ["content_hash"],
    )

    op.get_bind().execute(
        sa.text(
            f"""
            INSERT INTO experiment_plans (id, owner_subject, current_version, created_at)
            SELECT id, '__legacy_local__', version, created_at
            FROM {LEGACY_TABLE}
            """
        )
    )
    op.execute(
        sa.text(
            f"""
            INSERT INTO experiment_plan_versions (
                plan_id, version, specification_json, plan_json, validation_json,
                provenance_json, draft_json, legacy_project_id, legacy_environment_id,
                legacy_status, content_hash, hash_format_version, created_at, legacy_updated_at
            )
            SELECT
                id, version, normalized_brief, plan_json, validation_json,
                '[]'::jsonb, NULL, project_id, environment_id, status,
                plan_hash, 0, created_at, updated_at
            FROM {LEGACY_TABLE}
            """
        )
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF (SELECT count(*) FROM experiment_plan_versions)
               <> (SELECT count(*) FROM experiment_plans_legacy_m4_2) THEN
                RAISE EXCEPTION 'M4.2 migration row-count verification failed';
            END IF;
        END
        $$;
        """
    )
    op.create_foreign_key(
        "fk_experiment_plan_current_version",
        "experiment_plans",
        "experiment_plan_versions",
        ["id", "current_version"],
        ["plan_id", "version"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.execute(
        """
        CREATE FUNCTION reject_experiment_plan_version_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION 'Experiment plan versions are immutable'
                USING ERRCODE = '55000';
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER experiment_plan_versions_immutable
        BEFORE UPDATE OR DELETE ON experiment_plan_versions
        FOR EACH ROW EXECUTE FUNCTION reject_experiment_plan_version_mutation();
        """
    )
    op.drop_table(LEGACY_TABLE)


def downgrade() -> None:
    raise RuntimeError(
        "M4.2 immutable plan migration is forward-only; downgrade would discard "
        "owner and provenance information."
    )
