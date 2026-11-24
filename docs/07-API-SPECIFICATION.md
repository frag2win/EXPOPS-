# 07 --- Internal API Specification

**Version:** 0.1\
**Status:** Proposed; implement incrementally

All routes are under `/api/v1`. These are ExperimentOps endpoints, not
Flagsmith endpoints.

## 1. Health

### `GET /health`

Returns service health.

Response:

``` json
{
  "status": "ok",
  "service": "experimentops"
}
```

## 2. Connection

### `GET /connections/flagsmith/status`

Checks whether the backend has a configured Flagsmith connection and
whether authentication succeeds.

Never returns the secret.

### `GET /flagsmith/projects`

Lists accessible projects.

### `GET /flagsmith/projects/{project_id}/environments`

Lists environments for a verified project.

### `GET /flagsmith/environments/{environment_id}/preflight`

Checks the selected environment and returns prerequisite findings,
including warehouse readiness where supported.

## 3. Planning

### `POST /plans`

Creates a plan from a natural-language request.

Request:

``` json
{
  "prompt": "Test the existing checkout against a simplified checkout. Use 70% control and 30% simplified. Track successful purchases.",
  "project_id": "project-id",
  "environment_id": "environment-id"
}
```

Response:

``` json
{
  "plan_id": "plan_123",
  "version": 1,
  "status": "READY_FOR_APPROVAL",
  "summary": "Compare the current checkout with a simplified checkout.",
  "hypothesis": "The simplified checkout increases completed purchases.",
  "actions": [],
  "validation": {
    "errors": [],
    "warnings": []
  },
  "plan_hash": "sha256:..."
}
```

The response must contain the actual normalized plan and action
descriptions. `actions` is abbreviated here.

### `GET /plans/{plan_id}`

Returns plan, status, validation and execution state.

### `POST /plans/{plan_id}/validate`

Runs deterministic validation again against current state.

## 4. Approval

### `POST /plans/{plan_id}/approve`

Request:

``` json
{
  "version": 1,
  "plan_hash": "sha256:...",
  "approved": true
}
```

Approval is invalid if the plan version/hash changed, the user lacks
permission, or the plan has expired.

### `POST /plans/{plan_id}/reject`

Rejects the pending plan.

## 5. Execution

### `POST /plans/{plan_id}/execute`

Request:

``` json
{
  "approval_id": "approval_123",
  "idempotency_key": "client-generated-unique-key"
}
```

The backend verifies approval and revalidates before executing. It
returns an execution ID and current status. Long-running execution may
return `202 Accepted`.

### `GET /executions/{execution_id}`

Returns step-by-step progress and verification results.

## 6. Experiment status and results

### `GET /experiments/{experiment_id}`

Reads experiment details from Flagsmith through the adapter.

### `POST /experiments/{experiment_id}/refresh-results`

Requests a result refresh only if supported, permitted and not blocked
by a rate limit.

### `GET /experiments/{experiment_id}/results`

Returns the verified result payload and a plain-language explanation. If
no results are available, return an explicit pending state rather than
fabricated values.

## 7. Error response format

``` json
{
  "error": {
    "code": "EXPERIMENT_PREREQUISITE_MISSING",
    "message": "No warehouse connection was found for the selected environment.",
    "details": {
      "environment_id": "environment-id"
    },
    "next_action": "Connect a warehouse in Flagsmith, then rerun preflight."
  },
  "request_id": "req_123"
}
```

## 8. HTTP status conventions

-   `200`: successful read/update.
-   `201`: resource created.
-   `202`: accepted asynchronous execution.
-   `400`: malformed request.
-   `401`: unauthenticated.
-   `403`: not permitted.
-   `404`: resource not found.
-   `409`: stale plan, duplicate execution or state conflict.
-   `422`: validation failed.
-   `429`: rate limited.
-   `502/503`: upstream Flagsmith unavailable.

## 9. API contract rules

-   Use Pydantic schemas.
-   Reject unknown critical fields.
-   Do not return secrets.
-   Include request IDs.
-   Version the API.
-   Document endpoints through FastAPI OpenAPI.
-   Generate frontend types from the API schema where practical.
