"""Transactional persistence and integrity verification for plan snapshots."""

import hashlib
import hmac
import json
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import ExperimentPlanRecord, ExperimentPlanVersionRecord
from app.domain.experiments.draft_models import (
    ExperimentDraftResponse, FieldProvenance, ProvenanceSource,
)
from app.domain.experiments.models import ExperimentPlanResponse, ExperimentSpecification

CURRENT_HASH_FORMAT_VERSION = 1
LEGACY_HASH_FORMAT_VERSION = 0
LEGACY_OWNER_SUBJECT = "__legacy_local__"


class PlanIntegrityError(Exception):
    """Stored plan content does not match its persisted digest."""


def canonical_json(value: Any) -> bytes:
    """Serialize canonical JSON values deterministically, rejecting non-JSON numbers."""
    def normalize(item: Any) -> Any:
        if isinstance(item, Decimal):
            return str(item)
        if isinstance(item, Enum):
            return item.value
        if isinstance(item, UUID):
            return str(item)
        if hasattr(item, "model_dump"):
            return normalize(item.model_dump(mode="python"))
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise TypeError("Canonical JSON object keys must be strings")
            return {key: normalize(value) for key, value in item.items()}
        if isinstance(item, (list, tuple)):
            return [normalize(value) for value in item]
        if item is None or isinstance(item, (str, bool, int, float)):
            return item
        raise TypeError(f"Unsupported canonical JSON value: {type(item).__name__}")

    encoded = json.dumps(
        normalize(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return encoded.encode("utf-8")


def snapshot_payload(
    specification: dict,
    plan: dict,
    validation: dict,
    provenance: list,
    draft: dict | None,
) -> dict:
    return {
        "format": CURRENT_HASH_FORMAT_VERSION,
        "specification": specification,
        "plan_response": {
            "plan": plan,
            "validation": validation,
            "execution_enabled": False,
        },
        "provenance": provenance,
        "draft": draft,
    }


def content_hash(
    specification: dict,
    plan: dict,
    validation: dict,
    provenance: list,
    draft: dict | None = None,
) -> str:
    payload = snapshot_payload(specification, plan, validation, provenance, draft)
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def _legacy_content_hash(record: ExperimentPlanVersionRecord) -> str:
    legacy_response = {
        "plan": record.plan_json,
        "validation": record.validation_json,
        "execution_enabled": False,
    }
    encoded = json.dumps(
        legacy_response,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def verify_record_hash(record: ExperimentPlanVersionRecord) -> None:
    if record.hash_format_version == LEGACY_HASH_FORMAT_VERSION:
        calculated = _legacy_content_hash(record)
    elif record.hash_format_version == CURRENT_HASH_FORMAT_VERSION:
        calculated = content_hash(
            record.specification_json,
            record.plan_json,
            record.validation_json,
            record.provenance_json,
            record.draft_json,
        )
    else:
        raise PlanIntegrityError("Unsupported plan hash format.")
    if not hmac.compare_digest(calculated, record.content_hash):
        raise PlanIntegrityError("Stored plan integrity verification failed.")


async def create_plan(
    session: AsyncSession,
    owner_subject: str,
    response: ExperimentPlanResponse,
    provenance: list[FieldProvenance] | None = None,
    draft: ExperimentDraftResponse | None = None,
) -> tuple[ExperimentPlanRecord, ExperimentPlanVersionRecord]:
    """Create a plan identity and its first immutable version."""
    plan_id = uuid4()
    now = datetime.now(timezone.utc)
    version = _make_version(plan_id, 1, response, provenance or [], draft, now)
    identity = ExperimentPlanRecord(
        id=plan_id,
        owner_subject=owner_subject,
        current_version=1,
        created_at=now,
    )
    session.add(identity)
    session.add(version)
    await session.flush()
    return identity, version


def manual_provenance(specification: ExperimentSpecification) -> list[FieldProvenance]:
    """Derive provenance only from fields present in the structured request."""
    supplied = specification.model_dump(mode="json", exclude_unset=True)
    complete = specification.model_dump(mode="json")

    def flatten(value: Any, path: str = "") -> dict[str, Any]:
        if isinstance(value, dict) and value:
            return {
                child_path: child_value
                for key, child in value.items()
                for child_path, child_value in flatten(
                    child, f"{path}.{key}" if path else key,
                ).items()
            }
        if isinstance(value, list) and value:
            return {
                child_path: child_value
                for index, child in enumerate(value)
                for child_path, child_value in flatten(child, f"{path}[{index}]").items()
            }
        return {path: value} if path else {}

    supplied_paths = flatten(supplied)
    complete_paths = flatten(complete)
    return [
        FieldProvenance(
            field_path=path,
            source=(
                ProvenanceSource.USER_PROVIDED
                if path in supplied_paths
                else ProvenanceSource.UNKNOWN
            ),
            explanation=(
                "Explicitly supplied in the structured plan request."
                if path in supplied_paths
                else "No value was supplied for this field."
            ),
        )
        for path in complete_paths
    ]


async def append_plan_version(
    session: AsyncSession,
    plan_id: UUID,
    owner_subject: str,
    response: ExperimentPlanResponse,
    provenance: list[FieldProvenance] | None = None,
    draft: ExperimentDraftResponse | None = None,
) -> tuple[ExperimentPlanRecord, ExperimentPlanVersionRecord] | None:
    """Append a snapshot while serializing version allocation on the parent row."""
    statement = (
        select(ExperimentPlanRecord)
        .where(
            ExperimentPlanRecord.id == plan_id,
            ExperimentPlanRecord.owner_subject.in_((owner_subject, LEGACY_OWNER_SUBJECT)),
        )
        .with_for_update()
    )
    identity = await session.scalar(statement)
    if identity is None:
        return None
    version_number = identity.current_version + 1
    version = _make_version(
        identity.id, version_number, response, provenance or [], draft,
        datetime.now(timezone.utc),
    )
    session.add(version)
    identity.current_version = version_number
    await session.flush()
    return identity, version


def _make_version(
    plan_id: UUID,
    version_number: int,
    response: ExperimentPlanResponse,
    provenance: list[FieldProvenance],
    draft: ExperimentDraftResponse | None,
    created_at: datetime,
) -> ExperimentPlanVersionRecord:
    specification = response.plan.specification.model_dump(mode="json")
    plan = response.plan.model_dump(mode="json")
    validation = response.validation.model_dump(mode="json")
    provenance_json = [item.model_dump(mode="json") for item in provenance]
    draft_json = draft.model_dump(mode="json") if draft is not None else None
    return ExperimentPlanVersionRecord(
        plan_id=plan_id,
        version=version_number,
        specification_json=specification,
        plan_json=plan,
        validation_json=validation,
        provenance_json=provenance_json,
        draft_json=draft_json,
        content_hash=content_hash(specification, plan, validation, provenance_json, draft_json),
        hash_format_version=CURRENT_HASH_FORMAT_VERSION,
        created_at=created_at,
    )


async def get_plan(
    session: AsyncSession,
    plan_id: UUID,
    owner_subject: str,
) -> tuple[ExperimentPlanRecord, ExperimentPlanVersionRecord] | None:
    identity = await session.scalar(
        select(ExperimentPlanRecord).where(
            ExperimentPlanRecord.id == plan_id,
            ExperimentPlanRecord.owner_subject.in_((owner_subject, LEGACY_OWNER_SUBJECT)),
        )
    )
    if identity is None:
        return None
    version = await session.get(
        ExperimentPlanVersionRecord,
        (identity.id, identity.current_version),
    )
    if version is None:
        raise PlanIntegrityError("Current plan version is missing.")
    verify_record_hash(version)
    return identity, version


async def get_plan_version(
    session: AsyncSession,
    plan_id: UUID,
    version_number: int,
    owner_subject: str,
) -> tuple[ExperimentPlanRecord, ExperimentPlanVersionRecord] | None:
    identity = await session.scalar(
        select(ExperimentPlanRecord).where(
            ExperimentPlanRecord.id == plan_id,
            ExperimentPlanRecord.owner_subject.in_((owner_subject, LEGACY_OWNER_SUBJECT)),
        )
    )
    if identity is None:
        return None
    version = await session.get(ExperimentPlanVersionRecord, (plan_id, version_number))
    if version is None:
        return None
    verify_record_hash(version)
    return identity, version


async def list_plans(
    session: AsyncSession,
    owner_subject: str,
    *,
    offset: int,
    limit: int,
) -> tuple[list[dict[str, Any]], int]:
    """Load and hash-verify each paginated plan's current snapshot, not historical snapshots."""
    ownership = ExperimentPlanRecord.owner_subject.in_(
        (owner_subject, LEGACY_OWNER_SUBJECT),
    )
    total = await session.scalar(
        select(func.count()).select_from(ExperimentPlanRecord).where(ownership)
    )
    statement = (
        select(
            ExperimentPlanRecord,
            ExperimentPlanVersionRecord,
        )
        .join(
            ExperimentPlanVersionRecord,
            and_(
                ExperimentPlanVersionRecord.plan_id == ExperimentPlanRecord.id,
                ExperimentPlanVersionRecord.version == ExperimentPlanRecord.current_version,
            ),
        )
        .where(ownership)
        .order_by(ExperimentPlanRecord.created_at.desc(), ExperimentPlanRecord.id.desc())
        .offset(offset)
        .limit(limit)
    )
    result = await session.execute(statement)
    rows = []
    for identity, version in result.all():
        verify_record_hash(version)
        specification = version.specification_json
        validation = version.validation_json
        rows.append(
            {
                "id": identity.id,
                "current_version": identity.current_version,
                "created_at": identity.created_at,
                "updated_at": version.created_at,
                "name": specification.get("name"),
                "hypothesis": specification.get("hypothesis"),
                "project": specification.get("project"),
                "environment": specification.get("environment"),
                "feature_key": specification.get("feature_key"),
                "feature_id": specification.get("feature_id"),
                "readiness": validation.get("overall_status"),
                "validation_summary": validation.get("summary"),
            }
        )
    return rows, int(total or 0)


async def list_plan_versions(
    session: AsyncSession,
    plan_id: UUID,
    owner_subject: str,
    *,
    offset: int,
    limit: int,
) -> tuple[ExperimentPlanRecord, list[ExperimentPlanVersionRecord], int] | None:
    """Return one owner's versions in reverse version order, verifying each hash."""
    identity = await session.scalar(
        select(ExperimentPlanRecord).where(
            ExperimentPlanRecord.id == plan_id,
            ExperimentPlanRecord.owner_subject.in_(
                (owner_subject, LEGACY_OWNER_SUBJECT),
            ),
        )
    )
    if identity is None:
        return None

    total = await session.scalar(
        select(func.count())
        .select_from(ExperimentPlanVersionRecord)
        .where(ExperimentPlanVersionRecord.plan_id == plan_id)
    )
    result = await session.scalars(
        select(ExperimentPlanVersionRecord)
        .where(ExperimentPlanVersionRecord.plan_id == plan_id)
        .order_by(ExperimentPlanVersionRecord.version.desc())
        .offset(offset)
        .limit(limit)
    )
    versions = list(result.all())
    for version in versions:
        verify_record_hash(version)
    return identity, versions, int(total or 0)
