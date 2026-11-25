"""Milestone 1 health and read-only Flagsmith routes."""

from fastapi import APIRouter, Depends

from app.api.dependencies import get_flagsmith_adapter
from app.integrations.flagsmith.adapter import FlagsmithAdapter
from app.integrations.flagsmith.exceptions import FlagsmithAuthenticationError
from app.integrations.flagsmith.schemas import (
    ConnectionStatus,
    Environment,
    EnvironmentInfo,
    FeatureFlag,
    FlagsResponse,
    HealthResponse,
    Project,
)

router = APIRouter()


@router.get("/health", response_model=HealthResponse, tags=["health"])
async def health() -> HealthResponse:
    return HealthResponse()


@router.get("/api/v1/connections/flagsmith/status", response_model=ConnectionStatus, tags=["connections"])
async def flagsmith_status(adapter: FlagsmithAdapter = Depends(get_flagsmith_adapter)) -> ConnectionStatus:
    if not adapter.configured:
        return ConnectionStatus(configured=False, authenticated=False, message="Flagsmith API credentials are not configured")
    try:
        await adapter.verify_authentication()
    except FlagsmithAuthenticationError:
        return ConnectionStatus(configured=True, authenticated=False, message="Flagsmith authentication failed")
    return ConnectionStatus(configured=True, authenticated=True, message="Flagsmith authentication succeeded")


@router.get("/api/v1/flagsmith/projects", response_model=list[Project], tags=["flagsmith"])
async def projects(adapter: FlagsmithAdapter = Depends(get_flagsmith_adapter)) -> list[Project]:
    return await adapter.list_projects()


@router.get("/api/v1/flagsmith/projects/{project_id}/environments", response_model=list[Environment], tags=["flagsmith"])
async def environments(project_id: str, adapter: FlagsmithAdapter = Depends(get_flagsmith_adapter)) -> list[Environment]:
    return await adapter.list_environments(project_id)


@router.get("/api/v1/flagsmith/environments/{environment_id}", response_model=EnvironmentInfo, tags=["flagsmith"])
async def environment(environment_id: str, adapter: FlagsmithAdapter = Depends(get_flagsmith_adapter)) -> EnvironmentInfo:
    return await adapter.get_environment(environment_id)


@router.get("/api/v1/flagsmith/environments/{environment_id}/flags", response_model=FlagsResponse, tags=["flagsmith"])
async def flags(environment_id: str, adapter: FlagsmithAdapter = Depends(get_flagsmith_adapter)) -> FlagsResponse:
    return FlagsResponse(environment_id=environment_id, flags=await adapter.list_flags(environment_id))


@router.get("/api/v1/flagsmith/projects/{project_id}/flags/{feature_id}", response_model=FeatureFlag, tags=["flagsmith"])
async def flag_detail(project_id: str, feature_id: str, adapter: FlagsmithAdapter = Depends(get_flagsmith_adapter)) -> FeatureFlag:
    return await adapter.get_flag(project_id, feature_id)
