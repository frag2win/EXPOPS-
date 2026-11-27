"""Experiment planning and deterministic validation endpoints."""

from fastapi import APIRouter, Depends

from app.api.dependencies import get_flagsmith_adapter
from app.domain.experiments.models import (
    ExperimentEngineHealth, ExperimentPlanResponse, ExperimentSpecification,
    ValidationReport,
)
from app.integrations.flagsmith.adapter import FlagsmithAdapter
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
