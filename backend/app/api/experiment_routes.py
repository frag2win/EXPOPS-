"""Experiment planning and deterministic validation endpoints."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.dependencies import get_flagsmith_adapter
from app.db.runtime import session_scope, transaction_scope
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
from app.persistence.experiment_plans import (
    append_plan_version,
    create_plan,
    get_plan,
    get_plan_version,
    list_plan_versions,
    list_plans,
    manual_provenance,
)
from app.services.experiment_planning import ExperimentPlanningService

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


def _reject_owner_override(request: Request) -> None:
    if "owner_subject" in request.query_params:
        raise HTTPException(
            status_code=422,
            detail="The plan owner is determined by server configuration.",
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
    adapter: FlagsmithAdapter = Depends(get_flagsmith_adapter),
) -> PersistedExperimentPlan:
    runtime = request.app.state.database_runtime
    if runtime is None:
        raise HTTPException(status_code=503, detail="Plan persistence is not configured.")
    response = await ExperimentPlanningService(adapter).plan(spec)
    async with transaction_scope(runtime.session_factory) as session:
        identity, version = await create_plan(
            session,
            request.app.state.settings.local_owner_subject,
            response,
            provenance=manual_provenance(spec),
        )
    return _record_response(identity, version)


@router.post("/plans/{plan_id}/versions", response_model=PersistedExperimentPlan, status_code=status.HTTP_201_CREATED)
async def append_experiment_plan_version(
    plan_id: str,
    spec: ExperimentSpecification,
    request: Request,
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
            request.app.state.settings.local_owner_subject,
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
            request.app.state.settings.local_owner_subject,
            draft.planning,
            provenance=draft.provenance,
            draft=draft,
        )
    return _record_response(identity, version)


@router.get("/plans", response_model=PlanListResponse)
async def list_saved_experiment_plans(
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> PlanListResponse:
    _reject_owner_override(request)
    runtime = request.app.state.database_runtime
    if runtime is None:
        raise HTTPException(status_code=503, detail="Plan persistence is not configured.")
    offset = (page - 1) * page_size
    async with session_scope(runtime.session_factory) as session:
        rows, total = await list_plans(
            session,
            request.app.state.settings.local_owner_subject,
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
) -> PlanReviewResponse:
    _reject_owner_override(request)
    runtime = request.app.state.database_runtime
    if runtime is None:
        raise HTTPException(status_code=503, detail="Plan persistence is not configured.")
    try:
        parsed_id = UUID(plan_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Experiment plan was not found.") from None
    async with session_scope(runtime.session_factory) as session:
        result = await get_plan(session, parsed_id, request.app.state.settings.local_owner_subject)
    if result is None:
        raise HTTPException(status_code=404, detail="Experiment plan was not found.")
    return _review_response(*result)


@router.get("/plans/{plan_id}/versions", response_model=PlanVersionHistoryResponse)
async def get_experiment_plan_version_history(
    plan_id: str,
    request: Request,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> PlanVersionHistoryResponse:
    _reject_owner_override(request)
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
            request.app.state.settings.local_owner_subject,
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
) -> PlanReviewResponse:
    _reject_owner_override(request)
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
            request.app.state.settings.local_owner_subject,
        )
    if result is None:
        raise HTTPException(status_code=404, detail="Experiment plan was not found.")
    return _review_response(*result)


@router.post("/draft", response_model=ExperimentDraftResponse)
async def draft_experiment(
    payload: ExperimentDraftRequest,
    provider: StructuredOutputProvider = Depends(get_structured_output_provider),
    adapter: FlagsmithAdapter = Depends(get_flagsmith_adapter),
) -> ExperimentDraftResponse:
    agent = ExperimentSpecificationAgent(provider, ExperimentPlanningService(adapter))
    return await agent.draft(payload)
