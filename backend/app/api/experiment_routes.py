"""Experiment planning and deterministic validation endpoints."""

from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from pydantic import ValidationError

from app.api.dependencies import get_current_principal, get_flagsmith_adapter
from app.db.runtime import session_scope, transaction_scope
from app.domain.identity import Principal
from app.domain.experiments.models import (
    ExperimentEngineHealth, ExperimentPlanResponse, ExperimentSpecification,
    ValidationReport,
)
from app.integrations.flagsmith.adapter import FlagsmithAdapter
from app.agents.experiment_specification import ExperimentSpecificationAgent
from app.domain.experiments.draft_models import ExperimentDraftRequest, ExperimentDraftResponse
from app.llm.factory import get_structured_output_provider
from app.llm.protocols import StructuredOutputProvider
from app.domain.experiments.persistence_models import (
    PersistedExperimentPlan,
    PlanListItem,
    PlanListResponse,
    PlanListSummary,
    PlanReviewResponse,
    PlanReviewSummary,
    PlanVersionHistoryItem,
    PlanVersionHistoryResponse,
    ProvenanceSummary,
)
from app.domain.experiments.decision_models import (
    ApprovalRequest,
    DecisionHistoryResponse,
    DecisionType,
    ExperimentPlanDecisionResponse,
    RejectionRequest,
    RevalidationOutcome,
)
from app.persistence.experiment_plans import (
    PlanIntegrityError,
    append_plan_version,
    create_plan,
    get_plan,
    get_plan_version,
    list_plan_versions,
    list_plans,
    manual_provenance,
)
from app.persistence.experiment_decisions import (
    ApprovalRevalidationError,
    DecisionConflictError,
    DecisionIdempotencyConflictError,
    DecisionPlanNotFoundError,
    DecisionVersionNotFoundError,
    StaleApprovalRevalidationError,
    StalePlanVersionError,
    approval_blocking_reasons,
    build_revalidation_snapshot,
    decision_request_hash,
    get_idempotent_decision,
    get_owned_plan_version,
    get_plan_decision,
    get_plan_decisions,
    record_plan_decision,
    rejection_revalidation_snapshot,
)
from app.services.experiment_planning import ExperimentPlanningService
from app.integrations.flagsmith.exceptions import FlagsmithError

router = APIRouter(prefix="/api/v1/experiments", tags=["experiments"])


@router.get("/health", response_model=ExperimentEngineHealth)
async def experiment_health() -> ExperimentEngineHealth:
    return ExperimentEngineHealth()


@router.post("/validate", response_model=ValidationReport)
async def validate_experiment(spec: ExperimentSpecification, adapter: FlagsmithAdapter = Depends(get_flagsmith_adapter)) -> ValidationReport:
    return await ExperimentPlanningService(adapter).validate(spec)


@router.post("/plan", response_model=ExperimentPlanResponse)
async def plan_experiment(spec: ExperimentSpecification, adapter: FlagsmithAdapter = Depends(get_flagsmith_adapter)) -> ExperimentPlanResponse:
    return await ExperimentPlanningService(adapter).plan(spec)


def _record_response(identity, version) -> PersistedExperimentPlan:
    return PersistedExperimentPlan(
        id=identity.id,
        version=version.version,
        content_hash=version.content_hash,
        hash_format_version=version.hash_format_version,
        specification=version.specification_json,
        plan=version.plan_json,
        validation=version.validation_json,
        provenance=version.provenance_json,
        draft=version.draft_json,
        created_at=version.created_at,
    )


def _provenance_summary(provenance: list[dict]) -> ProvenanceSummary:
    counts = {
        "AI_INFERRED": 0,
        "USER_CONFIRMED": 0,
        "USER_PROVIDED": 0,
        "UNKNOWN": 0,
    }
    for item in provenance:
        source = item.get("source")
        if source in counts:
            counts[source] += 1
    return ProvenanceSummary(
        ai_inferred=counts["AI_INFERRED"],
        user_confirmed=counts["USER_CONFIRMED"],
        user_provided=counts["USER_PROVIDED"],
        unknown=counts["UNKNOWN"],
    )


def _blocking_reasons(validation: dict) -> list[str]:
    reasons = [
        item["message"]
        for item in validation.get("errors", [])
        if isinstance(item, dict) and isinstance(item.get("message"), str)
    ]
    reasons.extend(
        item["message"]
        for item in validation.get("checks", [])
        if isinstance(item, dict)
        and item.get("status") == "failed"
        and isinstance(item.get("message"), str)
    )
    return list(dict.fromkeys(reasons))


def _review_response(identity, version) -> PlanReviewResponse:
    specification = version.specification_json
    plan = version.plan_json
    validation = version.validation_json
    provenance = version.provenance_json
    summary = PlanReviewSummary(
        name=specification.get("name"),
        hypothesis=specification.get("hypothesis"),
        project=specification.get("project"),
        environment=specification.get("environment"),
        feature_key=specification.get("feature_key"),
        feature_id=specification.get("feature_id"),
        variants=specification.get("variants") or [],
        primary_metric=specification.get("primary_metric"),
        secondary_metrics=specification.get("secondary_metrics") or [],
        readiness=validation.get("overall_status"),
        validation_summary=validation.get("summary"),
        blocking_reasons=_blocking_reasons(validation),
        missing_information=validation.get("missing_information") or [],
        provenance=_provenance_summary(provenance),
    )
    return PlanReviewResponse(
        id=identity.id,
        current_version=identity.current_version,
        version=version.version,
        is_current=version.version == identity.current_version,
        created_at=identity.created_at,
        version_created_at=version.created_at,
        updated_at=version.created_at,
        content_hash=version.content_hash,
        hash_format_version=version.hash_format_version,
        summary=summary,
        specification=specification,
        plan=plan,
        validation=validation,
        provenance=provenance,
        draft=version.draft_json,
        integrity_status="verified",
    )


@router.post("/plans", response_model=PersistedExperimentPlan, status_code=status.HTTP_201_CREATED)
async def persist_experiment_plan(
    spec: ExperimentSpecification,
    request: Request,
    principal: Principal = Depends(get_current_principal),
    adapter: FlagsmithAdapter = Depends(get_flagsmith_adapter),
) -> PersistedExperimentPlan:
    runtime = request.app.state.database_runtime
    if runtime is None:
        raise HTTPException(status_code=503, detail="Plan persistence is not configured.")
    response = await ExperimentPlanningService(adapter).plan(spec)
    async with transaction_scope(runtime.session_factory) as session:
        identity, version = await create_plan(
            session,
            principal,
            response,
            provenance=manual_provenance(spec),
        )
    return _record_response(identity, version)


@router.post("/plans/{plan_id}/versions", response_model=PersistedExperimentPlan, status_code=status.HTTP_201_CREATED)
async def append_experiment_plan_version(
    plan_id: str,
    spec: ExperimentSpecification,
    request: Request,
    principal: Principal = Depends(get_current_principal),
    adapter: FlagsmithAdapter = Depends(get_flagsmith_adapter),
) -> PersistedExperimentPlan:
    runtime = request.app.state.database_runtime
    if runtime is None:
        raise HTTPException(status_code=503, detail="Plan persistence is not configured.")
    try:
        parsed_id = UUID(plan_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Experiment plan was not found.") from None
    response = await ExperimentPlanningService(adapter).plan(spec)
    async with transaction_scope(runtime.session_factory) as session:
        result = await append_plan_version(
            session,
            parsed_id,
            principal,
            response,
            provenance=manual_provenance(spec),
        )
    if result is None:
        raise HTTPException(status_code=404, detail="Experiment plan was not found.")
    return _record_response(*result)


@router.post("/drafts", response_model=PersistedExperimentPlan, status_code=status.HTTP_201_CREATED)
async def draft_and_persist_experiment_plan(
    payload: ExperimentDraftRequest,
    request: Request,
    principal: Principal = Depends(get_current_principal),
    provider: StructuredOutputProvider = Depends(get_structured_output_provider),
    adapter: FlagsmithAdapter = Depends(get_flagsmith_adapter),
) -> PersistedExperimentPlan:
    runtime = request.app.state.database_runtime
    if runtime is None:
        raise HTTPException(status_code=503, detail="Plan persistence is not configured.")
    agent = ExperimentSpecificationAgent(provider, ExperimentPlanningService(adapter))
    draft = await agent.draft(payload)
    async with transaction_scope(runtime.session_factory) as session:
        identity, version = await create_plan(
            session,
            principal,
            draft.planning,
            provenance=draft.provenance,
            draft=draft,
        )
    return _record_response(identity, version)


@router.get("/plans", response_model=PlanListResponse)
async def list_saved_experiment_plans(
    request: Request,
    principal: Principal = Depends(get_current_principal),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> PlanListResponse:
    runtime = request.app.state.database_runtime
    if runtime is None:
        raise HTTPException(status_code=503, detail="Plan persistence is not configured.")
    offset = (page - 1) * page_size
    async with session_scope(runtime.session_factory) as session:
        rows, total = await list_plans(
            session,
            principal,
            offset=offset,
            limit=page_size,
        )
    items = [
        PlanListItem(
            id=row["id"],
            current_version=row["current_version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            summary=PlanListSummary(
                name=row["name"],
                hypothesis=row["hypothesis"],
                project=row["project"],
                environment=row["environment"],
                feature_key=row["feature_key"],
                feature_id=row["feature_id"],
                readiness=row["readiness"],
                validation_summary=row["validation_summary"],
            ),
            integrity_status="verified",
        )
        for row in rows
    ]
    return PlanListResponse(items=items, page=page, page_size=page_size, total=total)


@router.get("/plans/{plan_id}", response_model=PlanReviewResponse)
async def get_persisted_experiment_plan(
    plan_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> PlanReviewResponse:
    runtime = request.app.state.database_runtime
    if runtime is None:
        raise HTTPException(status_code=503, detail="Plan persistence is not configured.")
    try:
        parsed_id = UUID(plan_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Experiment plan was not found.") from None
    async with session_scope(runtime.session_factory) as session:
        result = await get_plan(session, parsed_id, principal)
    if result is None:
        raise HTTPException(status_code=404, detail="Experiment plan was not found.")
    return _review_response(*result)


@router.get("/plans/{plan_id}/versions", response_model=PlanVersionHistoryResponse)
async def get_experiment_plan_version_history(
    plan_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> PlanVersionHistoryResponse:
    runtime = request.app.state.database_runtime
    if runtime is None:
        raise HTTPException(status_code=503, detail="Plan persistence is not configured.")
    try:
        parsed_id = UUID(plan_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Experiment plan was not found.") from None
    offset = (page - 1) * page_size
    async with session_scope(runtime.session_factory) as session:
        result = await list_plan_versions(
            session,
            parsed_id,
            principal,
            offset=offset,
            limit=page_size,
        )
    if result is None:
        raise HTTPException(status_code=404, detail="Experiment plan was not found.")
    identity, versions, total = result
    items = [
        PlanVersionHistoryItem(
            version=version.version,
            created_at=version.created_at,
            readiness=version.validation_json.get("overall_status"),
            content_hash=version.content_hash,
            hash_format_version=version.hash_format_version,
            provenance=_provenance_summary(version.provenance_json),
            integrity_status="verified",
        )
        for version in versions
    ]
    return PlanVersionHistoryResponse(
        id=identity.id,
        current_version=identity.current_version,
        items=items,
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get("/plans/{plan_id}/versions/{version_number}", response_model=PlanReviewResponse)
async def get_persisted_experiment_plan_version(
    plan_id: str,
    version_number: int,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> PlanReviewResponse:
    runtime = request.app.state.database_runtime
    if runtime is None:
        raise HTTPException(status_code=503, detail="Plan persistence is not configured.")
    try:
        parsed_id = UUID(plan_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Experiment plan was not found.") from None
    if version_number < 1:
        raise HTTPException(status_code=404, detail="Experiment plan was not found.")
    async with session_scope(runtime.session_factory) as session:
        result = await get_plan_version(
            session,
            parsed_id,
            version_number,
            principal,
        )
    if result is None:
        raise HTTPException(status_code=404, detail="Experiment plan was not found.")
    return _review_response(*result)


def _validated_idempotency_key(value: str) -> str:
    normalized = value.strip()
    if (
        not normalized
        or len(normalized) > 128
        or not normalized.isprintable()
    ):
        raise HTTPException(
            status_code=422,
            detail={
                "code": "INVALID_IDEMPOTENCY_KEY",
                "message": "A printable idempotency key of 1 to 128 characters is required.",
            },
        )
    return normalized


async def _submit_plan_decision(
    *,
    request: Request,
    response: Response,
    plan_id: str,
    version_number: int,
    principal: Principal,
    decision_type: DecisionType,
    idempotency_key: str,
    adapter: FlagsmithAdapter | None = None,
    rejection_reason: str | None = None,
) -> ExperimentPlanDecisionResponse:
    runtime = request.app.state.database_runtime
    if runtime is None:
        raise HTTPException(status_code=503, detail="Plan decision persistence is not configured.")
    try:
        parsed_id = UUID(plan_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Experiment plan was not found.") from None
    if version_number < 1:
        raise HTTPException(status_code=404, detail="Experiment plan version was not found.")

    key = _validated_idempotency_key(idempotency_key)
    request_hash = decision_request_hash(
        parsed_id,
        version_number,
        decision_type,
        rejection_reason,
    )
    async with session_scope(runtime.session_factory) as session:
        owned = await get_owned_plan_version(
            session,
            parsed_id,
            version_number,
            principal,
        )
        if owned is None:
            raise HTTPException(status_code=404, detail="Experiment plan was not found.")
        identity, version = owned
        try:
            existing = await get_idempotent_decision(
                session,
                principal,
                key,
                request_hash,
            )
        except DecisionIdempotencyConflictError:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "IDEMPOTENCY_KEY_CONFLICT",
                    "message": "The idempotency key was already used for a different request.",
                },
            ) from None
        if existing is not None:
            response.status_code = status.HTTP_200_OK
            return existing
        if identity.current_version != version_number:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "STALE_PLAN_VERSION",
                    "message": "A decision can only be recorded for the current plan version.",
                },
            )
        content_digest = version.content_hash

    if decision_type == DecisionType.APPROVED:
        if adapter is None:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "APPROVAL_REVALIDATION_UNAVAILABLE",
                    "message": "Approval revalidation could not be completed.",
                },
            )
        try:
            specification = ExperimentSpecification.model_validate(
                version.specification_json,
            )
        except ValidationError:
            raise PlanIntegrityError(
                "Stored plan specification is invalid.",
            ) from None
        try:
            planning = await ExperimentPlanningService(
                adapter,
            ).plan(specification)
        except FlagsmithError:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "APPROVAL_REVALIDATION_UNAVAILABLE",
                    "message": "Approval revalidation could not be completed.",
                },
            ) from None
        if planning.validation.specification != specification:
            raise PlanIntegrityError(
                "Revalidation did not use the stored plan specification.",
            )
        revalidation = build_revalidation_snapshot(
            parsed_id,
            version_number,
            content_digest,
            planning.validation,
        )
        if revalidation.outcome != RevalidationOutcome.PASSED:
            raise ApprovalRevalidationError(revalidation)
    else:
        revalidation = rejection_revalidation_snapshot(
            parsed_id,
            version_number,
            content_digest,
        )

    try:
        decision, was_idempotent = await record_plan_decision(
            runtime.session_factory,
            principal=principal,
            plan_id=parsed_id,
            plan_version=version_number,
            content_hash=content_digest,
            decision_type=decision_type,
            idempotency_key=key,
            revalidation=revalidation,
            rejection_reason=rejection_reason,
        )
    except DecisionPlanNotFoundError:
        raise HTTPException(status_code=404, detail="Experiment plan was not found.") from None
    except DecisionVersionNotFoundError:
        raise HTTPException(status_code=404, detail="Experiment plan version was not found.") from None
    except StalePlanVersionError:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "STALE_PLAN_VERSION",
                "message": "A decision can only be recorded for the current plan version.",
            },
        ) from None
    except StaleApprovalRevalidationError:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "STALE_APPROVAL_REVALIDATION",
                "message": "Approval evidence expired before the decision was recorded; retry approval.",
            },
        ) from None
    except DecisionConflictError:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "PLAN_VERSION_ALREADY_DECIDED",
                "message": "This plan version already has a terminal decision.",
            },
        ) from None
    except DecisionIdempotencyConflictError:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "IDEMPOTENCY_KEY_CONFLICT",
                "message": "The idempotency key was already used for a different request.",
            },
        ) from None
    response.status_code = (
        status.HTTP_200_OK if was_idempotent else status.HTTP_201_CREATED
    )
    return decision


@router.post(
    "/plans/{plan_id}/versions/{version_number}/approve",
    response_model=ExperimentPlanDecisionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def approve_experiment_plan_version(
    plan_id: str,
    version_number: int,
    request: Request,
    response: Response,
    _payload: ApprovalRequest | None = None,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    principal: Principal = Depends(get_current_principal),
    adapter: FlagsmithAdapter = Depends(get_flagsmith_adapter),
) -> ExperimentPlanDecisionResponse:
    try:
        return await _submit_plan_decision(
            request=request,
            response=response,
            plan_id=plan_id,
            version_number=version_number,
            principal=principal,
            decision_type=DecisionType.APPROVED,
            idempotency_key=idempotency_key,
            adapter=adapter,
        )
    except ApprovalRevalidationError as error:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "APPROVAL_REVALIDATION_FAILED",
                "message": "The plan did not pass approval revalidation.",
                "readiness": (
                    error.snapshot.readiness.value
                    if error.snapshot.readiness is not None
                    else None
                ),
                "blocking_reasons": approval_blocking_reasons(error.snapshot),
            },
        ) from None


@router.post(
    "/plans/{plan_id}/versions/{version_number}/reject",
    response_model=ExperimentPlanDecisionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def reject_experiment_plan_version(
    plan_id: str,
    version_number: int,
    payload: RejectionRequest,
    request: Request,
    response: Response,
    idempotency_key: str = Header(alias="Idempotency-Key"),
    principal: Principal = Depends(get_current_principal),
) -> ExperimentPlanDecisionResponse:
    return await _submit_plan_decision(
        request=request,
        response=response,
        plan_id=plan_id,
        version_number=version_number,
        principal=principal,
        decision_type=DecisionType.REJECTED,
        idempotency_key=idempotency_key,
        rejection_reason=payload.reason,
    )


@router.get(
    "/plans/{plan_id}/decisions",
    response_model=DecisionHistoryResponse,
)
async def list_experiment_plan_decisions(
    plan_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> DecisionHistoryResponse:
    runtime = request.app.state.database_runtime
    if runtime is None:
        raise HTTPException(status_code=503, detail="Plan decision persistence is not configured.")
    try:
        parsed_id = UUID(plan_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Experiment plan was not found.") from None
    async with session_scope(runtime.session_factory) as session:
        result = await get_plan_decisions(
            session,
            parsed_id,
            principal,
            offset=(page - 1) * page_size,
            limit=page_size,
        )
    if result is None:
        raise HTTPException(status_code=404, detail="Experiment plan was not found.")
    items, total = result
    return DecisionHistoryResponse(
        plan_id=parsed_id,
        items=items,
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get(
    "/plans/{plan_id}/decisions/{decision_id}",
    response_model=ExperimentPlanDecisionResponse,
)
async def get_experiment_plan_decision(
    plan_id: str,
    decision_id: str,
    request: Request,
    principal: Principal = Depends(get_current_principal),
) -> ExperimentPlanDecisionResponse:
    runtime = request.app.state.database_runtime
    if runtime is None:
        raise HTTPException(status_code=503, detail="Plan decision persistence is not configured.")
    try:
        parsed_plan_id = UUID(plan_id)
        parsed_decision_id = UUID(decision_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Experiment plan decision was not found.") from None
    async with session_scope(runtime.session_factory) as session:
        decision = await get_plan_decision(
            session,
            parsed_plan_id,
            parsed_decision_id,
            principal,
        )
    if decision is None:
        raise HTTPException(status_code=404, detail="Experiment plan decision was not found.")
    return decision


@router.post("/draft", response_model=ExperimentDraftResponse)
async def draft_experiment(
    payload: ExperimentDraftRequest,
    provider: StructuredOutputProvider = Depends(get_structured_output_provider),
    adapter: FlagsmithAdapter = Depends(get_flagsmith_adapter),
) -> ExperimentDraftResponse:
    agent = ExperimentSpecificationAgent(provider, ExperimentPlanningService(adapter))
    return await agent.draft(payload)
