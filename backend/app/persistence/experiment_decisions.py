"""Atomic, owner-scoped persistence for immutable human plan decisions."""

import hashlib
import hmac
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    ExperimentPlanDecisionAuditRecord,
    ExperimentPlanDecisionRecord,
    ExperimentPlanRecord,
    ExperimentPlanVersionRecord,
)
from app.db.runtime import transaction_scope
from app.domain.experiments.decision_models import (
    DecisionAuditResponse,
    DecisionRevalidationSnapshot,
    DecisionType,
    ExperimentPlanDecisionResponse,
    RevalidationOutcome,
)
from app.domain.experiments.models import (
    CheckStatus,
    PlanReadiness,
    ReadinessStatus,
    ValidationReport,
)
from app.domain.identity import Principal
from app.persistence.experiment_plans import (
    LEGACY_OWNER_SUBJECT,
    PlanIntegrityError,
    canonical_json,
    verify_record_hash,
)

DECISION_AUDIT_EVENT = "PLAN_DECISION_RECORDED"
APPROVAL_REVALIDATION_MAX_AGE = timedelta(seconds=60)


class DecisionConflictError(Exception):
    """A terminal decision already exists for the requested version."""


class DecisionIdempotencyConflictError(Exception):
    """An idempotency key was reused with a different request."""


class DecisionPlanNotFoundError(Exception):
    """The requested plan is missing or not owned by the active principal."""


class DecisionVersionNotFoundError(Exception):
    """The requested plan version does not exist."""


class StalePlanVersionError(Exception):
    """The requested version is no longer the current plan version."""


class ApprovalRevalidationError(Exception):
    """Deterministic revalidation did not establish approval readiness."""

    def __init__(self, snapshot: DecisionRevalidationSnapshot) -> None:
        self.snapshot = snapshot
        super().__init__("The plan did not pass approval revalidation.")


class StaleApprovalRevalidationError(Exception):
    """Approval evidence became stale before the decision transaction."""


def decision_request_hash(
    plan_id: UUID,
    plan_version: int,
    decision_type: DecisionType,
    rejection_reason: str | None,
) -> str:
    payload = {
        "plan_id": str(plan_id),
        "plan_version": plan_version,
        "decision_type": decision_type.value,
        "rejection_reason": rejection_reason,
    }
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def idempotency_reference(idempotency_key: str) -> str:
    return hashlib.sha256(idempotency_key.encode("utf-8")).hexdigest()


def decision_audit_hash(audit: ExperimentPlanDecisionAuditRecord) -> str:
    payload = {
        "format": 1,
        "id": str(audit.id),
        "decision_id": str(audit.decision_id),
        "plan_id": str(audit.plan_id),
        "plan_version": audit.plan_version,
        "event_type": audit.event_type,
        "decision_type": audit.decision_type,
        "actor_subject": audit.actor_subject,
        "occurred_at": audit.occurred_at.astimezone(timezone.utc).isoformat(),
        "content_hash": audit.content_hash,
        "revalidation_outcome": audit.revalidation_outcome,
        "rejection_reason": audit.rejection_reason,
        "idempotency_reference": audit.idempotency_reference,
        "metadata": audit.metadata_json,
    }
    return hashlib.sha256(canonical_json(payload)).hexdigest()


def _revalidation_is_fresh(
    snapshot: DecisionRevalidationSnapshot,
    reference_time: datetime,
) -> bool:
    checked_at = snapshot.revalidated_at
    if checked_at is None or checked_at.tzinfo is None:
        return False
    age = reference_time - checked_at.astimezone(timezone.utc)
    return timedelta(0) <= age <= APPROVAL_REVALIDATION_MAX_AGE


def build_revalidation_snapshot(
    plan_id: UUID,
    plan_version: int,
    content_hash: str,
    report: ValidationReport,
) -> DecisionRevalidationSnapshot:
    snapshot = DecisionRevalidationSnapshot(
        plan_id=plan_id,
        plan_version=plan_version,
        content_hash=content_hash,
        revalidated_at=datetime.now(timezone.utc),
        outcome=RevalidationOutcome.FAILED,
        readiness=report.overall_status,
        summary=report.summary,
        checks=report.checks,
        errors=report.errors,
        warnings=report.warnings,
        missing_information=report.missing_information,
        external_prerequisites=report.external_prerequisites,
    )
    if _revalidation_is_approvable(snapshot):
        snapshot.outcome = RevalidationOutcome.PASSED
    return snapshot


def _revalidation_is_approvable(
    snapshot: DecisionRevalidationSnapshot,
) -> bool:
    required_prerequisites = {
        "project",
        "environment",
        "feature",
        "feature_experiment_support",
        "warehouse",
    }
    return (
        snapshot.readiness == PlanReadiness.READY_FOR_REVIEW
        and not snapshot.errors
        and not snapshot.missing_information
        and not any(
            check.status in {CheckStatus.FAILED, CheckStatus.UNKNOWN}
            for check in snapshot.checks
        )
        and len(snapshot.external_prerequisites) == len(required_prerequisites)
        and {
            item.name for item in snapshot.external_prerequisites
        } == required_prerequisites
        and all(
            prerequisite.status == ReadinessStatus.AVAILABLE
            and prerequisite.independently_verified
            and bool(prerequisite.source.strip())
            and bool(prerequisite.evidence and prerequisite.evidence.strip())
            for prerequisite in snapshot.external_prerequisites
        )
    )


def approval_blocking_reasons(snapshot: DecisionRevalidationSnapshot) -> list[str]:
    reasons = [error.message for error in snapshot.errors]
    reasons.extend(
        check.message
        for check in snapshot.checks
        if check.status in {CheckStatus.FAILED, CheckStatus.UNKNOWN}
    )
    reasons.extend(f"Missing information: {field}" for field in snapshot.missing_information)
    reasons.extend(
        prerequisite.message
        for prerequisite in snapshot.external_prerequisites
        if prerequisite.status != ReadinessStatus.AVAILABLE
        or not prerequisite.independently_verified
    )
    if not reasons:
        reasons.append(snapshot.summary)
    return list(dict.fromkeys(reasons))


def rejection_revalidation_snapshot(
    plan_id: UUID,
    plan_version: int,
    content_hash: str,
) -> DecisionRevalidationSnapshot:
    return DecisionRevalidationSnapshot(
        plan_id=plan_id,
        plan_version=plan_version,
        content_hash=content_hash,
        outcome=RevalidationOutcome.NOT_RUN,
        summary="Revalidation is required for approval; rejection records the exact persisted snapshot.",
    )


async def get_owned_plan_version(
    session: AsyncSession,
    plan_id: UUID,
    plan_version: int,
    principal: Principal,
) -> tuple[ExperimentPlanRecord, ExperimentPlanVersionRecord] | None:
    identity = await session.scalar(
        select(ExperimentPlanRecord).where(
            ExperimentPlanRecord.id == plan_id,
            ExperimentPlanRecord.owner_subject.in_(
                (principal.subject, LEGACY_OWNER_SUBJECT),
            ),
        )
    )
    if identity is None:
        return None
    version = await session.get(
        ExperimentPlanVersionRecord,
        (plan_id, plan_version),
    )
    if version is None:
        return None
    verify_record_hash(version)
    return identity, version


async def _select_decision_bundle(
    session: AsyncSession,
    *,
    principal: Principal,
    plan_id: UUID | None = None,
    decision_id: UUID | None = None,
    idempotency_key: str | None = None,
) -> tuple[
    ExperimentPlanDecisionRecord,
    ExperimentPlanDecisionAuditRecord,
    ExperimentPlanVersionRecord,
] | None:
    statement = (
        select(
            ExperimentPlanDecisionRecord,
            ExperimentPlanDecisionAuditRecord,
            ExperimentPlanVersionRecord,
        )
        .join(
            ExperimentPlanRecord,
            ExperimentPlanRecord.id == ExperimentPlanDecisionRecord.plan_id,
        )
        .outerjoin(
            ExperimentPlanDecisionAuditRecord,
            ExperimentPlanDecisionAuditRecord.decision_id
            == ExperimentPlanDecisionRecord.id,
        )
        .join(
            ExperimentPlanVersionRecord,
            (
                ExperimentPlanVersionRecord.plan_id
                == ExperimentPlanDecisionRecord.plan_id
            )
            & (
                ExperimentPlanVersionRecord.version
                == ExperimentPlanDecisionRecord.plan_version
            ),
        )
        .where(
            ExperimentPlanRecord.owner_subject.in_(
                (principal.subject, LEGACY_OWNER_SUBJECT),
            ),
        )
    )
    if plan_id is not None:
        statement = statement.where(ExperimentPlanDecisionRecord.plan_id == plan_id)
    if decision_id is not None:
        statement = statement.where(ExperimentPlanDecisionRecord.id == decision_id)
    if idempotency_key is not None:
        statement = statement.where(
            ExperimentPlanDecisionRecord.principal_subject == principal.subject,
            ExperimentPlanDecisionRecord.idempotency_key == idempotency_key,
        )
    result = await session.execute(statement)
    row = result.first()
    if row is None:
        return None
    decision, audit, version = row
    if audit is None:
        raise PlanIntegrityError("Decision audit record is missing.")
    _verify_decision_bundle(decision, audit, version)
    return decision, audit, version


def _verify_decision_bundle(
    decision: ExperimentPlanDecisionRecord,
    audit: ExperimentPlanDecisionAuditRecord,
    version: ExperimentPlanVersionRecord,
) -> None:
    verify_record_hash(version)
    if not hmac.compare_digest(decision.content_hash, version.content_hash):
        raise PlanIntegrityError("Decision content hash does not match the stored plan version.")
    if decision.plan_id != version.plan_id or decision.plan_version != version.version:
        raise PlanIntegrityError("Decision points to a different plan version.")
    try:
        decision_type = DecisionType(decision.decision_type)
    except ValueError:
        raise PlanIntegrityError("Decision type is invalid.") from None
    if not isinstance(audit.metadata_json, dict):
        raise PlanIntegrityError("Decision audit metadata is invalid.")
    expected_request_hash = decision_request_hash(
        decision.plan_id,
        decision.plan_version,
        decision_type,
        decision.rejection_reason,
    )
    if not hmac.compare_digest(decision.request_hash, expected_request_hash):
        raise PlanIntegrityError("Decision request fingerprint is invalid.")
    try:
        revalidation = DecisionRevalidationSnapshot.model_validate(
            decision.revalidation_json,
        )
    except ValueError:
        raise PlanIntegrityError("Decision revalidation snapshot is invalid.") from None
    if (
        revalidation.plan_id != decision.plan_id
        or revalidation.plan_version != decision.plan_version
        or not hmac.compare_digest(revalidation.content_hash, decision.content_hash)
        or revalidation.outcome.value != decision.revalidation_outcome
    ):
        raise PlanIntegrityError("Decision revalidation does not match its plan version.")
    if not hmac.compare_digest(audit.audit_hash, decision_audit_hash(audit)):
        raise PlanIntegrityError("Decision audit integrity verification failed.")
    if audit.metadata_json != {
        "schema_version": 1,
        "request_hash": decision.request_hash,
    }:
        raise PlanIntegrityError("Decision audit metadata is invalid.")
    if (
        decision_type == DecisionType.APPROVED
        and (
            decision.rejection_reason is not None
            or decision.revalidation_outcome != RevalidationOutcome.PASSED.value
            or not _revalidation_is_approvable(revalidation)
            or not _revalidation_is_fresh(revalidation, decision.decided_at)
        )
    ) or (
        decision_type == DecisionType.REJECTED
        and (
            not decision.rejection_reason
            or not decision.rejection_reason.strip()
            or decision.revalidation_outcome != RevalidationOutcome.NOT_RUN.value
        )
    ):
        raise PlanIntegrityError("Decision record fields are inconsistent.")
    if (
        audit.decision_id != decision.id
        or audit.plan_id != decision.plan_id
        or audit.plan_version != decision.plan_version
        or audit.event_type != DECISION_AUDIT_EVENT
        or audit.decision_type != decision.decision_type
        or audit.actor_subject != decision.principal_subject
        or audit.occurred_at != decision.decided_at
        or not hmac.compare_digest(audit.content_hash, decision.content_hash)
        or audit.revalidation_outcome != decision.revalidation_outcome
        or audit.rejection_reason != decision.rejection_reason
        or not hmac.compare_digest(
            audit.idempotency_reference,
            idempotency_reference(decision.idempotency_key),
        )
        or audit.metadata_json.get("request_hash") != decision.request_hash
    ):
        raise PlanIntegrityError("Decision audit record does not match its decision.")


def _decision_response(
    decision: ExperimentPlanDecisionRecord,
    audit: ExperimentPlanDecisionAuditRecord,
) -> ExperimentPlanDecisionResponse:
    return ExperimentPlanDecisionResponse(
        id=decision.id,
        plan_id=decision.plan_id,
        plan_version=decision.plan_version,
        content_hash=decision.content_hash,
        decision_type=DecisionType(decision.decision_type),
        principal_subject=decision.principal_subject,
        decided_at=decision.decided_at,
        rejection_reason=decision.rejection_reason,
        revalidation=DecisionRevalidationSnapshot.model_validate(
            decision.revalidation_json,
        ),
        audit=DecisionAuditResponse(
            id=audit.id,
            event_type=audit.event_type,
            actor_subject=audit.actor_subject,
            occurred_at=audit.occurred_at,
            content_hash=audit.content_hash,
            audit_hash=audit.audit_hash,
            revalidation_outcome=RevalidationOutcome(audit.revalidation_outcome),
            idempotency_reference=audit.idempotency_reference,
            metadata=audit.metadata_json,
        ),
    )


async def get_idempotent_decision(
    session: AsyncSession,
    principal: Principal,
    idempotency_key: str,
    request_hash: str,
) -> ExperimentPlanDecisionResponse | None:
    bundle = await _select_decision_bundle(
        session,
        principal=principal,
        idempotency_key=idempotency_key,
    )
    if bundle is None:
        return None
    decision, audit, _ = bundle
    if not hmac.compare_digest(decision.request_hash, request_hash):
        raise DecisionIdempotencyConflictError(
            "The idempotency key was already used for a different request.",
        )
    return _decision_response(decision, audit)


async def record_plan_decision(
    session_factory,
    *,
    principal: Principal,
    plan_id: UUID,
    plan_version: int,
    content_hash: str,
    decision_type: DecisionType,
    idempotency_key: str,
    revalidation: DecisionRevalidationSnapshot,
    rejection_reason: str | None = None,
) -> tuple[ExperimentPlanDecisionResponse, bool]:
    request_hash = decision_request_hash(
        plan_id,
        plan_version,
        decision_type,
        rejection_reason,
    )
    async with transaction_scope(session_factory) as session:
        identity = await session.scalar(
            select(ExperimentPlanRecord)
            .where(
                ExperimentPlanRecord.id == plan_id,
                ExperimentPlanRecord.owner_subject.in_(
                    (principal.subject, LEGACY_OWNER_SUBJECT),
                ),
            )
            .with_for_update()
        )
        if identity is None:
            raise DecisionPlanNotFoundError()

        digest = hashlib.sha256(
            f"{principal.subject}\x00{idempotency_key}".encode("utf-8"),
        ).digest()
        lock_left = int.from_bytes(digest[:4], "big", signed=True)
        lock_right = int.from_bytes(digest[4:8], "big", signed=True)
        await session.execute(
            select(func.pg_advisory_xact_lock(lock_left, lock_right)),
        )

        existing = await _select_decision_bundle(
            session,
            principal=principal,
            idempotency_key=idempotency_key,
        )
        if existing is not None:
            decision, audit, _ = existing
            if not hmac.compare_digest(decision.request_hash, request_hash):
                raise DecisionIdempotencyConflictError(
                    "The idempotency key was already used for a different request.",
                )
            return _decision_response(decision, audit), True

        version = await session.get(
            ExperimentPlanVersionRecord,
            (plan_id, plan_version),
        )
        if version is None:
            raise DecisionVersionNotFoundError()
        verify_record_hash(version)
        if not hmac.compare_digest(version.content_hash, content_hash):
            raise PlanIntegrityError("Plan version changed after approval revalidation.")
        if identity.current_version != plan_version:
            raise StalePlanVersionError()

        existing_version_decision = await session.scalar(
            select(ExperimentPlanDecisionRecord).where(
                ExperimentPlanDecisionRecord.plan_id == plan_id,
                ExperimentPlanDecisionRecord.plan_version == plan_version,
            )
        )
        if existing_version_decision is not None:
            raise DecisionConflictError()
        if revalidation.plan_id != plan_id or revalidation.plan_version != plan_version:
            raise PlanIntegrityError("Decision revalidation references a different plan version.")
        if not hmac.compare_digest(revalidation.content_hash, version.content_hash):
            raise PlanIntegrityError("Decision revalidation hash does not match the plan version.")
        if decision_type == DecisionType.APPROVED:
            if (
                rejection_reason is not None
                or not _revalidation_is_approvable(revalidation)
                or revalidation.outcome != RevalidationOutcome.PASSED
            ):
                raise PlanIntegrityError("Approval requires successful deterministic revalidation.")
        elif (
            decision_type != DecisionType.REJECTED
            or rejection_reason is None
            or not rejection_reason.strip()
            or revalidation.outcome != RevalidationOutcome.NOT_RUN
        ):
            raise PlanIntegrityError("Rejection decision fields are invalid.")

        now = datetime.now(timezone.utc)
        if (
            decision_type == DecisionType.APPROVED
            and not _revalidation_is_fresh(revalidation, now)
        ):
            raise StaleApprovalRevalidationError()
        decision = ExperimentPlanDecisionRecord(
            id=uuid4(),
            plan_id=plan_id,
            plan_version=plan_version,
            content_hash=version.content_hash,
            decision_type=decision_type.value,
            principal_subject=principal.subject,
            decided_at=now,
            rejection_reason=rejection_reason,
            revalidation_outcome=revalidation.outcome.value,
            revalidation_json=revalidation.model_dump(mode="json"),
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        session.add(decision)
        await session.flush()
        audit = ExperimentPlanDecisionAuditRecord(
            id=uuid4(),
            decision_id=decision.id,
            plan_id=plan_id,
            plan_version=plan_version,
            event_type=DECISION_AUDIT_EVENT,
            decision_type=decision_type.value,
            actor_subject=principal.subject,
            occurred_at=now,
            content_hash=version.content_hash,
            audit_hash="",
            revalidation_outcome=revalidation.outcome.value,
            rejection_reason=rejection_reason,
            idempotency_reference=idempotency_reference(idempotency_key),
            metadata_json={
                "schema_version": 1,
                "request_hash": request_hash,
            },
        )
        audit.audit_hash = decision_audit_hash(audit)
        session.add(audit)
        await session.flush()
        return _decision_response(decision, audit), False


async def get_plan_decisions(
    session: AsyncSession,
    plan_id: UUID,
    principal: Principal,
    *,
    offset: int,
    limit: int,
) -> tuple[list[ExperimentPlanDecisionResponse], int] | None:
    owned_plan = await session.scalar(
        select(ExperimentPlanRecord.id).where(
            ExperimentPlanRecord.id == plan_id,
            ExperimentPlanRecord.owner_subject.in_(
                (principal.subject, LEGACY_OWNER_SUBJECT),
            ),
        )
    )
    if owned_plan is None:
        return None
    total = await session.scalar(
        select(func.count())
        .select_from(ExperimentPlanDecisionRecord)
        .join(
            ExperimentPlanRecord,
            ExperimentPlanRecord.id == ExperimentPlanDecisionRecord.plan_id,
        )
        .where(
            ExperimentPlanDecisionRecord.plan_id == plan_id,
            ExperimentPlanRecord.owner_subject.in_(
                (principal.subject, LEGACY_OWNER_SUBJECT),
            ),
        )
    )
    statement = (
        select(
            ExperimentPlanDecisionRecord,
            ExperimentPlanDecisionAuditRecord,
            ExperimentPlanVersionRecord,
        )
        .outerjoin(
            ExperimentPlanDecisionAuditRecord,
            ExperimentPlanDecisionAuditRecord.decision_id
            == ExperimentPlanDecisionRecord.id,
        )
        .join(
            ExperimentPlanRecord,
            ExperimentPlanRecord.id == ExperimentPlanDecisionRecord.plan_id,
        )
        .join(
            ExperimentPlanVersionRecord,
            (
                ExperimentPlanVersionRecord.plan_id
                == ExperimentPlanDecisionRecord.plan_id
            )
            & (
                ExperimentPlanVersionRecord.version
                == ExperimentPlanDecisionRecord.plan_version
            ),
        )
        .where(
            ExperimentPlanDecisionRecord.plan_id == plan_id,
            ExperimentPlanRecord.owner_subject.in_(
                (principal.subject, LEGACY_OWNER_SUBJECT),
            ),
        )
        .order_by(
            ExperimentPlanDecisionRecord.decided_at.desc(),
            ExperimentPlanDecisionRecord.id.desc(),
        )
        .offset(offset)
        .limit(limit)
    )
    rows = (await session.execute(statement)).all()
    responses = []
    for decision, audit, version in rows:
        if audit is None:
            raise PlanIntegrityError("Decision audit record is missing.")
        _verify_decision_bundle(decision, audit, version)
        responses.append(_decision_response(decision, audit))
    return responses, int(total or 0)


async def get_plan_decision(
    session: AsyncSession,
    plan_id: UUID,
    decision_id: UUID,
    principal: Principal,
) -> ExperimentPlanDecisionResponse | None:
    bundle = await _select_decision_bundle(
        session,
        principal=principal,
        plan_id=plan_id,
        decision_id=decision_id,
    )
    if bundle is None:
        return None
    decision, audit, _ = bundle
    return _decision_response(decision, audit)
