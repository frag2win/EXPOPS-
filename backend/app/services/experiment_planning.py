"""Read-only Flagsmith inspection and deterministic plan orchestration."""

from app.domain.experiments.models import (
    ExperimentPlan, ExperimentPlanResponse, ExperimentSpecification,
    PlanReadiness, PrerequisiteSnapshot, ReadinessStatus, ResolvedEnvironment,
    ResolvedFeature, ResolvedProject, ResolvedVariantOption, ProposedStep,
)
from app.domain.experiments.validator import validate_experiment
from app.integrations.flagsmith.adapter import FlagsmithAdapter
from app.integrations.flagsmith.exceptions import FlagsmithNotFoundError


class FlagsmithPrerequisiteInspector:
    """Resolve only existing projects, environments and project feature catalog rows."""

    def __init__(self, adapter: FlagsmithAdapter) -> None:
        self.adapter = adapter

    async def inspect(self, spec: ExperimentSpecification) -> PrerequisiteSnapshot:
        result = PrerequisiteSnapshot(
            warehouse_status=ReadinessStatus.UNKNOWN,
            warehouse_message="Warehouse readiness is UNKNOWN: this service has no verified warehouse connection check.",
        )
        if not spec.project or not spec.project.strip():
            result.project_message = "Project selection was not supplied, so Flagsmith project discovery was not performed."
            result.environment_message = "Environment inspection requires a selected project."
            result.feature_message = "Feature inspection requires a selected project and environment."
            return result
        projects = await self.adapter.list_projects()
        project_matches = [p for p in projects if spec.project in {p.id, p.name}]
        if not project_matches:
            result.project_status = ReadinessStatus.UNAVAILABLE
            result.project_message = "No accessible Flagsmith project matched the supplied project identifier or exact name."
            return result
        if len(project_matches) > 1:
            result.project_message = "The supplied project value matched multiple accessible projects; provide a unique project identifier."
            return result
        project = project_matches[0]
        result.project = ResolvedProject(id=project.id, name=project.name)
        result.project_status = ReadinessStatus.AVAILABLE
        result.project_message = "Project was returned by the authenticated Flagsmith project listing."

        if not spec.environment or not spec.environment.strip():
            result.environment_message = "Environment selection was not supplied, so environment discovery was not performed."
            result.feature_message = "Feature inspection requires a selected environment."
            return result

        environments = await self.adapter.list_environments(project.id)
        environment_matches = [e for e in environments if spec.environment in {e.id, e.name}]
        if not environment_matches:
            result.environment_status = ReadinessStatus.UNAVAILABLE
            result.environment_message = "No environment matched within the selected project."
            return result
        if len(environment_matches) > 1:
            result.environment_message = "The supplied environment value matched multiple environments; provide a unique environment UUID."
            return result
        environment = environment_matches[0]
        try:
            detail = await self.adapter.get_environment(environment.id)
        except FlagsmithNotFoundError:
            result.environment_status = ReadinessStatus.UNAVAILABLE
            result.environment_message = "The selected environment UUID could not be retrieved from Flagsmith."
            return result
        if detail.project_id is None:
            result.environment_status = ReadinessStatus.UNKNOWN
            result.environment_message = "Environment detail omitted its project identifier, so project ownership could not be independently confirmed."
            return result
        if detail.project_id != project.id:
            result.environment_status = ReadinessStatus.UNAVAILABLE
            result.environment_message = "The selected environment belongs to a different Flagsmith project."
            return result
        result.environment = ResolvedEnvironment(id=detail.id, name=detail.name, project_id=detail.project_id)
        result.environment_status = ReadinessStatus.AVAILABLE
        result.environment_message = "Environment was listed under the selected project and its UUID detail matched that project."

        if not spec.feature_key and not spec.feature_id:
            result.feature_message = "Feature reference was not supplied, so feature lookup was not performed."
            return result

        flags = await self.adapter.list_flags(detail.id)
        feature_matches = [f for f in flags if (spec.feature_id and f.id == spec.feature_id) or (spec.feature_key and f.name == spec.feature_key)]
        if not feature_matches:
            result.feature_status = ReadinessStatus.UNAVAILABLE
            result.feature_message = "No project feature matched the supplied feature key or identifier."
            return result
        if len(feature_matches) > 1:
            result.feature_message = "The supplied feature key matched multiple catalog entries; provide a unique feature identifier."
            return result
        feature = feature_matches[0]
        options = None if feature.multivariate_options is None else [
            ResolvedVariantOption(
                id=o.id, key=o.key, value_type=o.value_type, value=o.value,
                default_allocation=o.default_allocation,
            ) for o in feature.multivariate_options
        ]
        result.feature = ResolvedFeature(
            id=feature.id, name=feature.name, feature_type=feature.type,
            initial_value=feature.initial_value, multivariate_options=options,
        )
        result.feature_status = ReadinessStatus.AVAILABLE
        result.feature_message = "Feature was returned by the project feature catalog; this does not assert experiment support."
        if options is None:
            result.feature_experiment_status = ReadinessStatus.UNKNOWN
            result.feature_experiment_message = "Flagsmith did not return a multivariate option field; experiment support is unknown."
        elif not options:
            result.feature_experiment_status = ReadinessStatus.UNAVAILABLE
            result.feature_experiment_message = "The feature catalog reported no multivariate options for this feature."
        elif any(not o.key for o in options):
            result.feature_experiment_status = ReadinessStatus.UNAVAILABLE
            result.feature_experiment_message = "One or more multivariate options lack a key required for variant mapping."
        else:
            result.feature_experiment_status = ReadinessStatus.AVAILABLE
            result.feature_experiment_message = "The catalog returned keyed multivariate options for the selected feature."
        return result


class ExperimentPlanningService:
    def __init__(self, adapter: FlagsmithAdapter) -> None:
        self.inspector = FlagsmithPrerequisiteInspector(adapter)

    async def plan(self, spec: ExperimentSpecification) -> ExperimentPlanResponse:
        snapshot = await self.inspector.inspect(spec)
        report = validate_experiment(spec, snapshot)
        proposed = []
        if report.overall_status in {PlanReadiness.READY_FOR_REVIEW, PlanReadiness.NEEDS_INPUT, PlanReadiness.BLOCKED}:
            for variant in spec.variants or []:
                if variant.key and snapshot.feature and not any(o.key == variant.key for o in snapshot.feature.multivariate_options or []):
                    proposed.append(ProposedStep(step_id=f"variant:{variant.key}", kind="variant", summary=f"Review proposed feature variant '{variant.key}'."))
            if spec.primary_metric:
                proposed.append(ProposedStep(step_id="metric:primary", kind="metric", summary=f"Review primary metric '{spec.primary_metric.name or 'unnamed'}'."))
            proposed.append(ProposedStep(step_id="experiment:review", kind="experiment", summary="Review experiment proposal; execution is disabled."))
        plan = ExperimentPlan(
            summary="Deterministic, non-persistent planning proposal; no Flagsmith resources were modified.",
            specification=spec, resolved_project=snapshot.project,
            resolved_environment=snapshot.environment, resolved_feature=snapshot.feature,
            proposed_steps=proposed,
        )
        return ExperimentPlanResponse(plan=plan, validation=report)

    async def validate(self, spec: ExperimentSpecification):
        return validate_experiment(spec, await self.inspector.inspect(spec))
