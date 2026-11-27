# 04 --- Technical Design

**Version:** 0.1

> **Milestone 2 implementation note (2026-11-25):** Experiment planning is
> implemented as Pydantic domain models, a pure validator, a read-only
> Flagsmith prerequisite inspector and FastAPI routes. Plans are not persisted.
> See `docs/13-EXPERIMENT-PLANNING-AND-VALIDATION.md` for the actual contract.

## 1. Technology choices

> **Milestone 3 implementation note (2026-11-27):** An optional structured
> output provider feeds a provenance-aware draft service. Its output is
> validated by the existing Pydantic domain model and deterministic planner.
> There is no LLM tool access, persistence, approval, or write operation.
> See `docs/14-AI-ASSISTED-SPECIFICATION.md`.

> **M4.1 implementation note (2026-11-28):** Optional PostgreSQL
> infrastructure now uses SQLAlchemy 2 async sessions with Psycopg 3 and
> Alembic. Only connection configuration, engine/session lifecycle, an
> isolated database readiness check, and an empty baseline migration exist.
> No plan, approval, execution, or audit tables exist yet. Existing M1–M3
> endpoints do not require database configuration.

  -----------------------------------------------------------------------
  Layer                   Choice                  Reason
  ----------------------- ----------------------- -----------------------
  Language                Python 3.12+            Existing project
                                                  strengths; typed
                                                  backend

  API                     FastAPI                 Async support,
                                                  validation, OpenAPI

  Schemas                 Pydantic v2             Strict input/output
                                                  contracts

  Frontend                React + TypeScript +    Fast demo UI
                          Vite                    

  Database                PostgreSQL              Durable plans,
                                                  approvals and audit

  ORM/migrations          SQLAlchemy 2 + Alembic  Mature relational
                                                  persistence

  Tests                   pytest + HTTPX          Backend and adapter
                                                  tests

  Agent                   Provider-agnostic       Avoid vendor lock-in
                          tool-calling interface  

  External platform       Flagsmith Management    Actual feature
                          API / CLI               management and
                                                  experiment
                                                  configuration
  -----------------------------------------------------------------------

Pin dependency versions once implementation begins. Do not use floating
`latest` versions in the final demo environment.

## 2. Suggested backend layout

``` text
backend/
├── app/
│   ├── main.py
│   ├── core/
│   │   ├── config.py
│   │   ├── logging.py
│   │   └── security.py
│   ├── api/
│   │   └── routes/
│   │       ├── health.py
│   │       ├── connections.py
│   │       ├── projects.py
│   │       ├── plans.py
│   │       ├── approvals.py
│   │       └── experiments.py
│   ├── domain/
│   │   ├── experiment.py
│   │   ├── plan.py
│   │   └── validation.py
│   ├── agent/
│   │   ├── orchestrator.py
│   │   ├── prompts.py
│   │   └── tools.py
│   ├── integrations/
│   │   └── flagsmith/
│   │       ├── client.py
│   │       ├── adapter.py
│   │       ├── schemas.py
│   │       └── exceptions.py
│   ├── services/
│   │   ├── planning.py
│   │   ├── execution.py
│   │   └── audit.py
│   └── db/
│       ├── models.py
│       ├── session.py
│       └── repositories/
├── tests/
│   ├── unit/
│   ├── integration/
│   └── contract/
├── pyproject.toml
└── Dockerfile
```

## 3. Suggested frontend layout

``` text
frontend/
├── src/
│   ├── app/
│   ├── components/
│   │   ├── ExperimentComposer.tsx
│   │   ├── PlanPreview.tsx
│   │   ├── ValidationPanel.tsx
│   │   ├── ApprovalPanel.tsx
│   │   ├── ExecutionTimeline.tsx
│   │   └── ResultsSummary.tsx
│   ├── pages/
│   ├── api/
│   ├── types/
│   └── main.tsx
├── package.json
└── vite.config.ts
```

## 4. Domain objects

### ExperimentBrief

A normalized representation of the developer's intent. It must be
independent of Flagsmith API response shapes.

### ExperimentPlan

A proposed sequence of typed actions, expected resource changes,
validation findings and required approvals.

### PlanAction

An allowlisted action such as: - inspect environment; - create/update
feature; - add/update variant; - create metric; - create experiment; -
start experiment; - refresh results.

Only actions verified as supported by the selected Flagsmith version may
be enabled.

### Approval

Binds a user to a specific plan ID, plan version and content hash.

### ExecutionStep

Records one action, its request metadata (redacted), response summary,
verification result and timestamps.

## 5. Error handling

Define typed errors: - `FlagsmithAuthenticationError` -
`FlagsmithPermissionError` - `FlagsmithRateLimitError` -
`FlagsmithNotFoundError` - `FlagsmithValidationError` -
`FlagsmithUnavailableError` - `ExperimentPrerequisiteError` -
`PlanValidationError` - `ApprovalRequiredError` -
`PlanVersionMismatchError`

Never convert an unknown upstream error into a success-shaped response.

## 6. Idempotency and partial failure

-   Reads may be retried with bounded backoff.
-   Before creating a named resource, inspect whether it already exists.
-   Do not assume name uniqueness is guaranteed by Flagsmith.
-   Store external IDs immediately after successful creation.
-   If a multi-step workflow fails halfway, report completed steps and
    provide a safe recovery plan.
-   Do not automatically delete resources as compensation unless the
    user explicitly approves a rollback plan.
-   Use an internal idempotency key for execution requests.

## 7. Observability

Log: - request ID; - plan ID; - action type; - external resource ID; -
duration; - status; - error category.

Never log: - API keys; - Authorization headers; - raw environment
files; - full LLM context containing secrets; - unredacted request
bodies if they may contain credentials.

## 8. Configuration

LLM configuration is optional: `GEMINI_API_KEY`, `GEMINI_MODEL`, and bounded
`LLM_TIMEOUT_SECONDS`. The initial provider is Google Gemini through the
`google-genai` SDK with native JSON Schema response output. Provider failures
are mapped to typed safe errors. Prompts are sent to the configured Google
Gemini API; retention and processing follow the applicable Google API account
and policy. Never log prompt, provider response body, API key, or authorization
header.

Use environment variables for local development. `.env` must be ignored
by Git. `.env.example` contains names and safe placeholders only.

`DATABASE_URL` is optional and is parsed as `SecretStr`; M4.1 accepts the
`postgresql+psycopg://` scheme. The async engine is created during application
lifespan only when configured, uses bounded pooling and a five-second connect
timeout, and is disposed at shutdown. Psycopg 3 async requires a Selector loop
on Windows. `backend/run_windows.py` uses a scoped `asyncio` loop factory for
the Uvicorn server without changing the global event-loop policy; use its
single-process, no-reload mode on Windows. Alembic's online migration runner
and the opt-in PostgreSQL test use the same per-run platform loop selection.
Linux and macOS continue to use their normal asyncio loop. Database connectivity is reported by
`GET /health/database`; it does not change the meaning of `GET /health`.
`backend/alembic.ini` loads the URL from application settings rather than a
committed connection string. See `docs/08-DATABASE-DESIGN.md` for local setup
and migration commands. The initial migration is empty.

Keep Flagsmith credentials on the backend. For a single-user hackathon
demo, a server-side environment variable is acceptable; a multi-user
deployment would require per-user OAuth or encrypted secret storage.
