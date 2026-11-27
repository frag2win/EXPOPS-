"""Translate verified Flagsmith resources into internal response models."""

from collections.abc import Mapping
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.integrations.flagsmith.client import FlagsmithClient
from app.integrations.flagsmith.exceptions import FlagsmithInvalidResponseError
from app.integrations.flagsmith.schemas import Environment, EnvironmentInfo, FeatureFlag, FeatureVariantOption, Project


def _mapping(value: object) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise FlagsmithInvalidResponseError("Flagsmith returned an unexpected resource shape")
    return value


ModelT = TypeVar("ModelT", bound=BaseModel)


def _validated(model: type[ModelT], value: object) -> ModelT:
    try:
        return model.model_validate(_mapping(value))
    except (ValidationError, TypeError) as exc:
        raise FlagsmithInvalidResponseError("Flagsmith returned a resource missing required fields") from exc


class FlagsmithAdapter:
    """Read-only interface for discovery operations used by ExperimentOps."""

    def __init__(self, client: FlagsmithClient) -> None:
        self.client = client

    @property
    def configured(self) -> bool:
        return self.client.configured

    async def verify_authentication(self) -> bool:
        await self.client.get_all("/api/v1/projects/")
        return True

    async def list_projects(self) -> list[Project]:
        values = await self.client.get_all("/api/v1/projects/")
        result = []
        for value in values:
            item = _mapping(value)
            result.append(_validated(Project, {"id": str(item.get("id")), "name": item.get("name"), "uuid": item.get("uuid")}))
        return result

    async def list_environments(self, project_id: str) -> list[Environment]:
        project = self.client.path_segment(project_id)
        values = await self.client.get_all(f"/api/v1/projects/{project}/environments/")
        return [self._environment(v) for v in values]

    async def get_environment(self, environment_id: str) -> EnvironmentInfo:
        environment_uuid = self.client.path_segment(environment_id)
        value = await self.client.get_json(f"/api/v1/environments/get-by-uuid/{environment_uuid}/")
        item = _mapping(value)
        normalized = {
            "id": str(item.get("uuid", environment_id)),
            "name": item.get("name"),
            "project_id": str(item["project"]) if item.get("project") is not None else None,
            "description": item.get("description"),
        }
        return _validated(EnvironmentInfo, normalized)

    async def list_flags(self, environment_id: str) -> list[FeatureFlag]:
        """List project flags associated with an environment.

        The Management API exposes the project feature catalog separately from
        environment feature states. This read returns the catalog; it does not
        claim to enumerate per-identity overrides or environment values.
        """
        environment = await self.get_environment(environment_id)
        if not environment.project_id:
            raise FlagsmithInvalidResponseError("Flagsmith environment response did not include its project")
        project = self.client.path_segment(environment.project_id)
        values = await self.client.get_all(f"/api/v1/projects/{project}/features/")
        return [self._feature(v) for v in values]

    async def get_flag(self, project_id: str, feature_id: str) -> FeatureFlag:
        project = self.client.path_segment(project_id)
        feature = self.client.path_segment(feature_id)
        value = await self.client.get_json(f"/api/v1/projects/{project}/features/{feature}/")
        return self._feature(value)

    @staticmethod
    def _environment(value: object) -> Environment:
        item = _mapping(value)
        normalized = {
            "id": str(item.get("uuid", item.get("id"))),
            "name": item.get("name"),
            "project_id": str(item["project"]) if item.get("project") is not None else None,
        }
        return _validated(Environment, normalized)

    @staticmethod
    def _feature(value: object) -> FeatureFlag:
        item = _mapping(value)
        options = item.get("multivariate_options")
        parsed_options = None
        if options is not None:
            if not isinstance(options, list):
                raise FlagsmithInvalidResponseError("Flagsmith returned an unexpected feature option shape")
            parsed_options = []
            for raw in options:
                option = _mapping(raw)
                kind = option.get("type")
                field = {"int": ("integer", "integer_value"), "unicode": ("string", "string_value"), "bool": ("boolean", "boolean_value")}.get(kind)
                if field is None or option.get("id") is None or field[1] not in option or option[field[1]] is None:
                    raise FlagsmithInvalidResponseError("Flagsmith returned an unsupported feature option")
                parsed_options.append(_validated(FeatureVariantOption, {
                    "id": str(option.get("id")), "key": option.get("key"),
                    "value_type": field[0], "value": option[field[1]],
                    "default_allocation": option.get("default_percentage_allocation"),
                }))
        normalized = {
            "id": str(item.get("id")),
            "name": item.get("name"),
            "description": item.get("description"),
            "type": item.get("type"),
            "initial_value": item.get("initial_value"),
            "enabled": item.get("enabled"),
            "multivariate_options": parsed_options,
        }
        return _validated(FeatureFlag, normalized)
