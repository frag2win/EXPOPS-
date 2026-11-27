"""ExperimentOps-owned schemas; never expose raw upstream models."""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Project(ApiModel):
    id: str
    name: str
    uuid: str | None = None


class Environment(ApiModel):
    id: str
    name: str
    project_id: str | None = None


class EnvironmentInfo(Environment):
    description: str | None = None


class FeatureVariantOption(ApiModel):
    id: str
    key: str | None = None
    value_type: Literal["integer", "string", "boolean"]
    value: StrictStr | StrictInt | StrictBool
    default_allocation: Decimal | None = Field(default=None, ge=0, le=100)


class FeatureFlag(ApiModel):
    id: str
    name: str
    description: str | None = None
    type: str | None = None
    initial_value: str | int | float | bool | None = None
    enabled: bool | None = None
    multivariate_options: list[FeatureVariantOption] | None = None


class ConnectionStatus(ApiModel):
    configured: bool
    authenticated: bool
    message: str


class FlagsResponse(ApiModel):
    environment_id: str
    flags: list[FeatureFlag] = Field(default_factory=list)


class HealthResponse(ApiModel):
    status: str = "ok"
    service: str = "experimentops"


class ErrorDetail(ApiModel):
    code: str
    message: str


class ErrorResponse(ApiModel):
    error: ErrorDetail
    request_id: str
