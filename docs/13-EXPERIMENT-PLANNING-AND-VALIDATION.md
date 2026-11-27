# Experiment Planning and Deterministic Validation

**Status:** Implemented, ephemeral planning only
**Verification date:** 2026-11-25

## Scope and architecture

The planning feature accepts a structured experiment specification and returns
a reviewable plan plus a deterministic validation report. It consists of
Pydantic v2 models (`app/domain/experiments/models.py`), a pure rules engine
(`validator.py`), a read-only Flagsmith prerequisite inspector and orchestration
service (`app/services/experiment_planning.py`), and routes in
`app/api/experiment_routes.py`. Plans are not persisted. No LLM is involved.
This document describes the deterministic Milestone 2 contract; the separate
Milestone 3 drafting endpoint is documented in
[`14-AI-ASSISTED-SPECIFICATION.md`](14-AI-ASSISTED-SPECIFICATION.md).

The inspector uses only the existing GET-based Flagsmith adapter to resolve an
accessible project, an environment listed under that project and confirmed by
UUID detail, and a feature in the project's catalog. It inspects the catalog's
typed `multivariate_options`; feature existence alone does not establish
experiment support. The live `ExperimentOps Demo` / `Development` feature
`checkout_simplified` was returned as `STANDARD` with an explicitly empty
multivariate option list. That feature is therefore blocked for experiment
planning. No resource was created or changed.

## Input model

`ExperimentSpecification` accepts optional fields so incomplete but
well-formed input can receive a useful `NEEDS_INPUT` report:

| Field | Meaning |
| --- | --- |
| `name`, `description`, `hypothesis` | Human-authored experiment intent |
| `project`, `environment` | Exact project/environment name or returned identifier |
| `feature_key` or `feature_id` | Exactly one existing catalog feature reference |
| `variants[]` | `key`, `role` (`control`/`treatment`), scalar `value`, Decimal `allocation` |
| `primary_metric` | `name`, exact case-sensitive `event_name`, `aggregation`, `direction` |
| `secondary_metrics[]` | Optional metric definitions, checked for duplicates |
| `instrumentation` | SDK readiness/evidence, exposure and conversion identity strategies, exposure requirement and conversion event |

Metric aggregation currently supports `occurrence` and direction supports `up`
or `down`, consistent with the verified Flagsmith experiment setup contract.
This API does not create or register metrics. Missing values are not inferred.
Allocations are parsed as Decimal and must already total exactly 100; they are
never rescaled or normalized.

## Deterministic rules

- Experiment name is nonempty, at most 120 characters, and has no control
  characters. Hypothesis, project and environment are required.
- Exactly one of feature key or ID is supplied; project, environment and
  feature are resolved through read-only discovery.
- There must be exactly one control and at least one treatment, each with a
  unique nonempty key, explicit value, and positive allocation no greater than
  100. Allocations sum exactly to 100. Control/treatment values must differ.
- Existing same-key Flagsmith options must have the same typed value. Proposed
  new keys are clearly labelled as not yet created. Values must be compatible
  with the selected feature's option types.
- The primary metric requires a name, event, supported aggregation and
  direction. Event comparisons preserve case. Duplicate definitions are
  detected by exact event, aggregation and direction.
- Exposure and conversion use the same identity strategy. The conversion event
  must exactly equal the primary metric event. SDK readiness must be supplied;
  an operator declaration requires evidence and is labelled as unverified by
  this service.
- Warehouse readiness is always UNKNOWN because no verified warehouse status
  integration exists. App source/SDK instrumentation is not independently
  inspected. Neither state is assumed true.

## Readiness states

| State | Meaning |
| --- | --- |
| `INVALID` | Contradictory or invalid specification values |
| `NEEDS_INPUT` | Required information or external evidence is missing/unknown |
| `BLOCKED` | A required external prerequisite is confirmed unavailable |
| `READY_FOR_REVIEW` | Supplied specification and currently established prerequisites pass; still not approval or execution |

Invalid values take precedence over external blockers. Confirmed blocked
prerequisites take precedence over missing information. Unknown prerequisites
are listed as missing information, not successful checks. A READY result is
possible in deterministic tests when prerequisites are explicitly established;
the current production inspector cannot establish warehouse readiness, so a
live plan will remain `NEEDS_INPUT` or `BLOCKED` until that integration exists.

Every check includes a stable rule ID, status, field and explanation/evidence.
Reports also include errors, warnings, missing information, external
prerequisites, resolved resources and recommended next steps.

## API

### `GET /api/v1/experiments/health`

Returns engine status, `engine: deterministic`, and
`write_operations_enabled: false`.

### `POST /api/v1/experiments/validate`

Accepts an `ExperimentSpecification` and returns a `ValidationReport` after
read-only prerequisite inspection.

### `POST /api/v1/experiments/plan`

Accepts the same specification and returns `{plan, validation,
execution_enabled: false}`. Proposed steps are explicitly
`proposed_not_executed` and require future approval. No plan ID, persistence,
approval or write route is implemented.

Well-formed but incomplete/invalid domain input returns HTTP 200 with the
report's readiness and check details. Malformed types or forbidden fields
return sanitized HTTP 422 without echoing body values. Flagsmith errors retain
the existing safe status/code mapping; upstream headers and bodies are not
returned.

## Current limitations and deferred work

- The sandbox feature `checkout_simplified` is standard and has no multivariate
  options; it is not experiment-ready based on current evidence.
- No warehouse integration exists; readiness remains UNKNOWN.
- SDK instrumentation is operator-declared only and is never asserted from
  source inspection.
- The deterministic planning engine itself has no LLM dependency. Milestone 3
  adds a separate optional draft route. There is no frontend, persistence,
  approval workflow, feature/metric/experiment creation, start/stop, results,
  or Flagsmith write operation.
- A `READY_FOR_REVIEW` response is a planning result only and is not an
  executed experiment or evidence of real traffic.
