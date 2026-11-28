"""Pure deterministic checks for proposed experiment specifications."""

from decimal import Decimal

from app.domain.experiments.models import (
    CheckStatus, ExperimentSpecification, PlanReadiness, PrerequisiteSnapshot,
    ReadinessStatus, ValidationCheck, ValidationMessage, ValidationReport,
    VariantRole,
)


def validate_experiment(spec: ExperimentSpecification, prerequisites: PrerequisiteSnapshot) -> ValidationReport:
    checks: list[ValidationCheck] = []
    errors: list[ValidationMessage] = []
    warnings: list[ValidationMessage] = []
    missing: list[str] = []
    blocked = False

    def issue(code: str, message: str, field: str | None, *, invalid: bool = False, block: bool = False) -> None:
        nonlocal blocked
        errors.append(ValidationMessage(code=code, message=message, field=field))
        checks.append(ValidationCheck(rule_id=code, status=CheckStatus.FAILED, message=message, field=field))
        blocked = blocked or block

    def need(code: str, message: str, field: str) -> None:
        if field not in missing:
            missing.append(field)
        checks.append(ValidationCheck(rule_id=code, status=CheckStatus.UNKNOWN, message=message, field=field))

    def passed(code: str, message: str, field: str | None = None, evidence: str | None = None) -> None:
        checks.append(ValidationCheck(rule_id=code, status=CheckStatus.PASSED, message=message, field=field, evidence=evidence))

    for field, label in (("name", "Experiment name"), ("hypothesis", "Hypothesis"), ("project", "Project"), ("environment", "Environment")):
        value = getattr(spec, field)
        if value is None or not value.strip():
            need("SPEC_REQUIRED", f"{label} is required.", field)
    if spec.name and (len(spec.name.strip()) > 120 or any(ord(c) < 32 for c in spec.name)):
        issue("SPEC_NAME_INVALID", "Experiment name must be at most 120 characters and contain no control characters.", "name", invalid=True)
    if not spec.feature_key and not spec.feature_id:
        need("FEATURE_REQUIRED", "Select an existing Flagsmith feature by key or identifier.", "feature_key")
    elif spec.feature_key and spec.feature_id:
        issue("FEATURE_REFERENCE_AMBIGUOUS", "Provide either feature_key or feature_id, not both.", "feature_key", invalid=True)

    variants = spec.variants or []
    keys = [v.key for v in variants if v.key and v.key.strip()]
    if len(keys) != len(set(keys)):
        issue("VARIANT_KEYS_UNIQUE", "Variant keys must be unique, preserving case.", "variants", invalid=True)
    if spec.variants is None:
        need("VARIANTS_REQUIRED", "Provide one control and at least one treatment variant.", "variants")
    else:
        controls = [v for v in variants if v.role == VariantRole.CONTROL]
        treatments = [v for v in variants if v.role == VariantRole.TREATMENT]
        if not controls:
            need("CONTROL_REQUIRED", "Exactly one control variant is required.", "variants")
        elif len(controls) != 1:
            issue("CONTROL_COUNT", "Exactly one control variant is allowed.", "variants", invalid=True)
        if not treatments:
            need("TREATMENT_REQUIRED", "At least one treatment variant is required.", "variants")
        for index, variant in enumerate(variants):
            field = f"variants.{index}"
            if not variant.key or not variant.key.strip():
                need("VARIANT_KEY_REQUIRED", "Every variant requires a non-empty key.", f"{field}.key")
            if variant.role is None:
                need("VARIANT_ROLE_REQUIRED", "Every variant must be identified as control or treatment.", f"{field}.role")
            if variant.value is None:
                need("VARIANT_VALUE_REQUIRED", "Every variant requires an explicit value.", f"{field}.value")
            if variant.allocation is None:
                need("ALLOCATION_REQUIRED", "Every variant requires an allocation percentage.", f"{field}.allocation")
            elif not variant.allocation.is_finite() or variant.allocation <= 0 or variant.allocation > 100:
                issue("ALLOCATION_RANGE", "Allocation must be finite and greater than 0 and at most 100.", f"{field}.allocation", invalid=True)
        if variants and all(v.allocation is not None and v.allocation.is_finite() for v in variants):
            total = sum((v.allocation for v in variants if v.allocation is not None), Decimal(0))
            if total != Decimal("100"):
                issue("ALLOCATION_TOTAL", f"Allocations total {total}%; they must total exactly 100%.", "variants", invalid=True)
            else:
                passed("ALLOCATION_TOTAL", "Variant allocations total exactly 100%.", "variants", f"total={total}")
        values = [_typed_value(v.value) for v in variants if v.value is not None]
        if len(values) != len(set(values)):
            issue("VARIANT_VALUES_DISTINCT", "Control and treatment values must be distinguishable.", "variants", invalid=True)

    metric = spec.primary_metric
    if metric is None:
        need("PRIMARY_METRIC_REQUIRED", "A primary metric is required.", "primary_metric")
    else:
        for attr, label in (("name", "Primary metric name"), ("event_name", "Primary metric event name"), ("aggregation", "Metric aggregation"), ("direction", "Metric direction")):
            value = getattr(metric, attr)
            if value is None or not value.strip():
                need("METRIC_FIELD_REQUIRED", f"{label} is required.", f"primary_metric.{attr}")
        if metric.aggregation and metric.aggregation != "occurrence":
            issue("METRIC_AGGREGATION_UNSUPPORTED", "Only the verified occurrence aggregation is supported for planning.", "primary_metric.aggregation", invalid=True)
        if metric.direction and metric.direction not in {"up", "down"}:
            issue("METRIC_DIRECTION_INVALID", "Metric direction must be 'up' or 'down'.", "primary_metric.direction", invalid=True)
        if metric.event_name is not None and not metric.event_name.strip():
            issue("METRIC_EVENT_EMPTY", "Metric event name cannot be empty.", "primary_metric.event_name", invalid=True)
    metrics = ([metric] if metric else []) + spec.secondary_metrics
    seen: set[tuple[str | None, str | None, str | None]] = set()
    for index, definition in enumerate(metrics):
        key = (definition.event_name, definition.aggregation, definition.direction)
        if key in seen:
            issue("METRIC_DUPLICATE", "Metric definitions must be unique by exact event name, aggregation, and direction.", f"metrics.{index}", invalid=True)
        seen.add(key)

    inst = spec.instrumentation
    if inst.sdk_readiness.value == "unknown":
        need("SDK_READINESS_UNKNOWN", "SDK instrumentation readiness must be confirmed by the operator.", "instrumentation.sdk_readiness")
    elif inst.sdk_readiness.value == "not_ready":
        issue("SDK_NOT_READY", "SDK instrumentation is confirmed not ready.", "instrumentation.sdk_readiness", block=True)
    elif not inst.sdk_evidence or not inst.sdk_evidence.strip():
        need("SDK_EVIDENCE_REQUIRED", "Provide evidence for the operator-declared SDK readiness.", "instrumentation.sdk_evidence")
    else:
        warnings.append(ValidationMessage(code="SDK_OPERATOR_ATTESTATION", message="SDK readiness is operator-attested and has not been independently inspected by this service.", field="instrumentation.sdk_readiness"))
        checks.append(ValidationCheck(rule_id="SDK_OPERATOR_ATTESTATION", status=CheckStatus.WARNING, message="SDK readiness is operator-attested and not independently verified.", field="instrumentation.sdk_readiness"))
    exposure_identity = inst.exposure_identity_strategy
    conversion_identity = inst.conversion_identity_strategy
    if not exposure_identity or not exposure_identity.strip():
        need("EXPOSURE_IDENTITY_REQUIRED", "Specify the stable identity used for exposure events.", "instrumentation.exposure_identity_strategy")
    if not conversion_identity or not conversion_identity.strip():
        need("CONVERSION_IDENTITY_REQUIRED", "Specify the stable identity used for conversion events.", "instrumentation.conversion_identity_strategy")
    if exposure_identity and conversion_identity and exposure_identity != conversion_identity:
        issue("IDENTITY_STRATEGY_MISMATCH", "Exposure and conversion events must use the same stable identity strategy.", "instrumentation", invalid=True)
    elif exposure_identity and conversion_identity:
        passed("IDENTITY_STRATEGY_MATCH", "Exposure and conversion events declare the same identity strategy.", "instrumentation")
    if not inst.exposure_event_requirement or not inst.exposure_event_requirement.strip():
        need("EXPOSURE_EVENT_REQUIRED", "Describe the required exposure event instrumentation.", "instrumentation.exposure_event_requirement")
    if not inst.conversion_event_name or not inst.conversion_event_name.strip():
        need("CONVERSION_EVENT_REQUIRED", "Specify the conversion event name.", "instrumentation.conversion_event_name")
    elif metric and metric.event_name and inst.conversion_event_name != metric.event_name:
        issue("CONVERSION_EVENT_MISMATCH", "Conversion event name must exactly match the primary metric event name.", "instrumentation.conversion_event_name", invalid=True)
    elif metric and metric.event_name and inst.conversion_event_name:
        passed("CONVERSION_EVENT_MATCH", "Conversion event name matches the primary metric event name exactly.", "instrumentation.conversion_event_name")

    external = []
    def prerequisite(name: str, status: ReadinessStatus, source: str, message: str, evidence: str | None = None) -> None:
        from app.domain.experiments.models import ExternalPrerequisite
        external.append(ExternalPrerequisite(name=name, status=status, source=source, message=message, evidence=evidence, independently_verified=status != ReadinessStatus.UNKNOWN))
        if status == ReadinessStatus.UNAVAILABLE:
            issue(f"PREREQUISITE_{name.upper()}_UNAVAILABLE", message, name.lower(), block=True)
        elif status == ReadinessStatus.UNKNOWN:
            need(f"PREREQUISITE_{name.upper()}_UNKNOWN", message, name.lower())
        else:
            passed(f"PREREQUISITE_{name.upper()}_AVAILABLE", message, name.lower(), evidence)

    prerequisite("project", prerequisites.project_status, "Flagsmith Management API", prerequisites.project_message, prerequisites.project.name if prerequisites.project else None)
    prerequisite("environment", prerequisites.environment_status, "Flagsmith Management API", prerequisites.environment_message, prerequisites.environment.name if prerequisites.environment else None)
    prerequisite("feature", prerequisites.feature_status, "Flagsmith project feature catalog", prerequisites.feature_message, prerequisites.feature.name if prerequisites.feature else None)
    feature_experiment_evidence = None
    if prerequisites.feature and prerequisites.feature.multivariate_options:
        feature_experiment_evidence = ", ".join(
            sorted(
                option.key
                for option in prerequisites.feature.multivariate_options
                if option.key
            )
        ) or None
    prerequisite(
        "feature_experiment_support",
        prerequisites.feature_experiment_status,
        "Flagsmith multivariate feature options",
        prerequisites.feature_experiment_message,
        feature_experiment_evidence,
    )
    prerequisite(
        "warehouse",
        prerequisites.warehouse_status,
        "No verified warehouse integration",
        prerequisites.warehouse_message,
        prerequisites.warehouse_evidence,
    )

    if prerequisites.feature and prerequisites.feature_status == ReadinessStatus.AVAILABLE:
        options = prerequisites.feature.multivariate_options
        if options:
            option_by_key = {o.key: o for o in options if o.key}
            option_types = {o.value_type for o in options}
            for index, variant in enumerate(variants):
                if variant.value is not None:
                    value_type = _value_type(variant.value)
                    if value_type not in option_types:
                        issue("FEATURE_VARIANT_TYPE_MISMATCH", f"Variant '{variant.key or index}' value type is incompatible with the selected feature options.", f"variants.{index}.value", invalid=True)
                option = option_by_key.get(variant.key)
                if option and variant.value is not None:
                    if _typed_value(option.value) != _typed_value(variant.value):
                        issue("FEATURE_VARIANT_VALUE_MISMATCH", f"Requested variant '{variant.key}' does not match its existing Flagsmith option value.", f"variants.{index}.value", invalid=True)
            for variant in variants:
                if variant.key and variant.key not in option_by_key:
                    warnings.append(ValidationMessage(code="VARIANT_OPTION_PROPOSED", message=f"Variant '{variant.key}' is a proposed option and is not present in Flagsmith; it has not been created.", field="variants"))
        elif options == []:
            issue("FEATURE_NOT_MULTIVARIATE", "The selected feature has no multivariate options and is not established as experiment-ready.", "feature_key", block=True)
        else:
            need("FEATURE_OPTIONS_UNKNOWN", "Flagsmith did not expose a verified multivariate option list for this feature.", "feature_key")

    statuses = {c.status for c in checks}
    if CheckStatus.FAILED in statuses and errors:
        overall = PlanReadiness.INVALID if any(code not in {"PREREQUISITE_PROJECT_UNAVAILABLE", "PREREQUISITE_ENVIRONMENT_UNAVAILABLE", "PREREQUISITE_FEATURE_UNAVAILABLE", "PREREQUISITE_FEATURE_EXPERIMENT_SUPPORT_UNAVAILABLE", "PREREQUISITE_WAREHOUSE_UNAVAILABLE", "SDK_NOT_READY"} for code in [e.code for e in errors]) else PlanReadiness.BLOCKED
    elif blocked:
        overall = PlanReadiness.BLOCKED
    elif missing:
        overall = PlanReadiness.NEEDS_INPUT
    else:
        overall = PlanReadiness.READY_FOR_REVIEW
    if overall == PlanReadiness.INVALID and blocked:
        # Contradictory input remains invalid even when external blockers also exist.
        pass
    summary = {
        PlanReadiness.INVALID: "The specification contains invalid or contradictory values.",
        PlanReadiness.BLOCKED: "A required external prerequisite is confirmed unavailable.",
        PlanReadiness.NEEDS_INPUT: "Required information or an unverifiable prerequisite is missing.",
        PlanReadiness.READY_FOR_REVIEW: "Planning checks passed; this is a reviewable proposal and no experiment was executed.",
    }[overall]
    next_steps = []
    if missing:
        next_steps.append("Supply or verify the listed missing information and run validation again.")
    if blocked:
        next_steps.append("Resolve confirmed unavailable prerequisites before seeking review.")
    if overall == PlanReadiness.READY_FOR_REVIEW:
        next_steps.append("Review the proposed plan and obtain required human approval before any future execution workflow.")
    if not next_steps:
        next_steps.append("Correct the reported validation errors and submit the specification again.")
    return ValidationReport(
        overall_status=overall, summary=summary, specification=spec,
        resolved_project=prerequisites.project, resolved_environment=prerequisites.environment,
        resolved_feature=prerequisites.feature, variant_allocation=variants,
        primary_metric=metric, checks=checks, errors=errors, warnings=warnings,
        missing_information=missing, external_prerequisites=external,
        recommended_next_steps=next_steps,
    )


def _typed_value(value: object) -> tuple[type, object]:
    return (type(value), value)


def _value_type(value: object) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, str):
        return "string"
    return "unsupported"
