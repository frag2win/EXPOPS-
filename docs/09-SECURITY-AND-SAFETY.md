# 09 --- Security and Safety Requirements

**Version:** 0.1

## 1. Threat model

The most important risks are: - accidental production changes; - leaked
Flagsmith credentials; - prompt injection through user input or resource
descriptions; - invalid LLM-generated configuration; - duplicate or
partially completed writes; - false claims that an experiment started or
produced results; - unauthorized user approval; - logging sensitive API
payloads.

## 2. Mandatory controls

### Credential protection

-   Keep Flagsmith API keys on the backend only.
-   Never include secrets in LLM prompts, tool descriptions, frontend
    state or logs.
-   Store secrets in environment variables for local development.
-   Ignore `.env` and credential files in Git.
-   Use a dedicated non-production project/environment for the demo.

### Approval

-   No write operation without explicit human approval.
-   Approval must be bound to plan ID, plan version and plan hash.
-   Any plan change invalidates approval.
-   Starting/rolling out an experiment should be a separately visible
    action.
-   Default to non-production environments.

### Tool restrictions

-   No arbitrary shell execution from model output.
-   No arbitrary URL fetching.
-   No dynamic API path construction from untrusted model text.
-   Use fixed tool functions with typed arguments.
-   Enforce an allowlist of supported operations.
-   Validate every tool input server-side.

### Prompt injection

-   Treat descriptions and API-returned text as untrusted data.
-   Never treat external content as system instructions.
-   Do not allow a feature description to authorize a write.
-   Keep policy and execution logic outside the model.

### Audit

-   Record actor, action, target resource, approval and outcome.
-   Redact tokens and secrets.
-   Preserve partial-failure details.

## 3. Environment safety

MVP defaults: - Only allow a configured test project/environment. - Show
the project and environment prominently before approval. - Block
production writes unless an explicit configuration flag is enabled. -
Never infer an environment from a natural-language phrase alone.

## 4. Failure safety

-   Stop on unknown API responses.
-   Do not retry non-idempotent writes blindly.
-   Verify after every write.
-   Report partial completion accurately.
-   Never automatically delete a created resource as rollback without an
    approved recovery plan.
-   If warehouse or SDK prerequisites are missing, block the experiment
    setup or clearly separate configuration-only steps from experiment
    readiness.

## 5. Honest result reporting

The UI must distinguish: - `CONFIGURED`: resources exist. - `RUNNING`:
Flagsmith confirms the experiment is running. - `WAITING_FOR_EVENTS`:
experiment is running but relevant events/results are not yet
available. - `RESULTS_AVAILABLE`: result payload is returned. -
`INCONCLUSIVE`: results exist but evidence is insufficient or uncertain
according to the source. - `FAILED`: external operation failed.

Never display generated sample metrics as real results. If a local
demonstration harness is used, label its data as synthetic/test traffic
in the UI and report.
