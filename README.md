# ExperimentOps

**Long-term product vision:** an AI-assisted, approval-first control plane
for managing Flagsmith experiments. Milestone 3 adds optional LLM-assisted
specification drafting; deterministic validation remains authoritative.

The long-term goal is to turn a developer's intent into a validated,
reviewable plan and support approved management-plane operations through
Flagsmith. Current implementation also offers an optional AI-assisted draft;
the implemented workflow still stops before approval and execution.

> **Project status:** Milestones 1 and 2, plus Milestone 3 AI-assisted
> specification drafting, M4.2 immutable plan persistence, and M4.3 read-only
> plan review are implemented. Drafts remain subject to user review. There is
> no approval workflow, Flagsmith write, execution, or frontend.

## The problem

Developers can manage feature flags and experiments through the
Flagsmith dashboard and APIs, but a multi-step experiment still requires
understanding the project's current state, choosing a safe
configuration, checking prerequisites, creating the required resources,
and tracking the experiment lifecycle.

ExperimentOps aims to make that workflow safer and easier without
pretending that an LLM can infer missing facts or replace the
application's instrumentation.

## Product boundary

The following describes the intended product, not completed functionality:

ExperimentOps is **not**: - a replacement for Flagsmith; - a game or
application analytics SDK; - a tool that fabricates experiment traffic
or results; - a generic chat interface that only writes instructions; -
an autonomous production-change bot.

ExperimentOps **is**: - a natural-language interface for planning
Flagsmith experiments; - a read-before-write workflow that inspects the
connected Flagsmith project; - a validator that checks prerequisites and
configuration consistency; - an approval-gated executor for supported
Flagsmith operations; - an audit trail for plans, approvals, execution
and outcomes.

## Intended MVP workflow (not implemented end-to-end)

1.  Developer describes the experiment.
2.  Agent extracts the hypothesis, variants, allocation, success metric,
    environment and application event requirements.
3.  Agent inspects the connected Flagsmith project and environment.
4.  Agent checks prerequisites, including experimentation/warehouse
    readiness.
5.  Agent produces a structured plan and validation report.
6.  Developer explicitly approves the plan.
7.  Backend executes the supported operations through the Flagsmith
    Management API / CLI.
8.  Agent reports the actual execution result and links to the
    experiment.
9.  Once real application events arrive, the user can refresh and
    inspect the experiment results.

**Important:** creating an experiment does not create real user traffic.
Exposures and conversion events must come from a real instrumented
application or a clearly labelled local test harness. Any test traffic
must be explicitly identified as test data.

## Proposed stack

-   Backend: Python 3.12+, FastAPI, Pydantic
-   Frontend: React + TypeScript + Vite
-   Persistence: PostgreSQL (SQLite may be used for the first local
    spike if time is constrained)
-   Agent orchestration: provider-agnostic tool-calling layer; start
    with a single agent and deterministic tools
-   Flagsmith: Management API and/or supported Flagsmith CLI commands
-   Tests: pytest, HTTPX, frontend unit tests
-   Local development: Docker Compose

The LLM must never hold a raw Flagsmith admin key in its prompt.
Credentials stay server-side and tools enforce authorization and
approval.

## Repository map

``` text
.
├── README.md
├── .env.example
├── .gitignore
├── docs/
│   ├── 01-PRD.md
│   ├── 02-SRS.md
│   ├── 03-SYSTEM-ARCHITECTURE.md
│   ├── 04-TECHNICAL-DESIGN.md
│   ├── 05-AGENT-DESIGN.md
│   ├── 06-FLAGSMITH-INTEGRATION.md
│   ├── 07-API-SPECIFICATION.md
│   ├── 08-DATABASE-DESIGN.md
│   ├── 09-SECURITY-AND-SAFETY.md
│   ├── 10-MVP-ROADMAP.md
│   ├── 11-TESTING-STRATEGY.md
│   ├── 12-HACKATHON-SUBMISSION.md
│   └── 13-EXPERIMENT-PLANNING-AND-VALIDATION.md
├── backend/
├── frontend/
├── tests/
└── scripts/
```

## First milestone

Do not start with a complex multi-agent framework. First prove one
vertical slice:

-   connect to a non-production Flagsmith environment;
-   read the project/environment and current flags;
-   accept an experiment brief;
-   generate a plan without writing;
-   validate the plan;
-   require approval;
-   execute one supported operation;
-   verify the result by reading Flagsmith again.

Milestone 1 provides read-only discovery. Milestone 2 adds structured
specifications, deterministic validation, a reviewable proposal and explicit
prerequisite status. Milestone 3 optionally drafts fields from natural
language and marks provenance and confirmation requirements. A
`READY_FOR_REVIEW` plan is not approved or executed.
Warehouse readiness and SDK instrumentation are unknown unless supplied as
operator evidence; the backend does not independently verify either.

## Run the backend

Requirements: Python 3.12 or newer. Copy `.env.example` to `.env` in the
repository root and set `FLAGSMITH_API_KEY` to an organization Management
API token. Keep the token server-side; the API never returns it. The
default Cloud URL is `https://api.flagsmith.com`; set
`FLAGSMITH_API_URL` for a self-hosted instance.

The template's `DATABASE_URL` uses the PostgreSQL Psycopg 3 driver and explicit
placeholders. Database configuration is optional. When configured, M4.1
creates an async SQLAlchemy engine during application lifespan and exposes
`GET /health/database`; existing M1–M3 endpoints do not require PostgreSQL.
M4.2 stores stable plan identities separately from immutable version
snapshots. Apply migrations before using persistence routes. Set the
server-side `LOCAL_OWNER_SUBJECT` (default `local-development`) to select the
local principal; clients cannot choose an owner. Snapshot retrieval verifies a
versioned canonical SHA-256 hash. The dedicated `POST
/api/v1/experiments/drafts` workflow invokes the server-side drafting agent
and persists its complete generated envelope and provenance; the existing
`POST /api/v1/experiments/draft` remains ephemeral. Plan versions are protected
against database `UPDATE` and `DELETE` by a PostgreSQL trigger. No persistence
route approves, rejects, executes, or modifies Flagsmith resources.
Migrated ownerless legacy records retain a reserved local marker and remain
visible to the active configured local principal after principal changes.
M4.3 adds a paginated, read-only plan list, current-plan review, and paginated
version history. List summaries are selected from each paginated current row;
their hashes are verified without loading historical snapshots. Current-plan,
history-page, and exact-version reads also recompute and verify stored hashes.
These responses report `integrity_status: "verified"` only after verification.
Integrity failures return a sanitized `PLAN_INTEGRITY_FAILED` response. List
and history pagination use `page` (default `1`) and `page_size` (default `20`,
maximum `100`). No review endpoint changes plan state.
Put real local credentials only in the ignored root
`.env`, never in `.env.example` or documentation. See
[`docs/08-DATABASE-DESIGN.md`](docs/08-DATABASE-DESIGN.md) for setup and
migration commands.

AI drafting is disabled unless a real Gemini API key is configured in the
ignored `.env`:

```ini
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-3.5-flash-lite
LLM_TIMEOUT_SECONDS=20
```

The provider uses Google's `google-genai` SDK and Gemini native JSON Schema
output with model `gemini-3.5-flash-lite` by default. `POST
/api/v1/experiments/draft` sends the submitted natural-language request,
constraints, and optional structured specification to Google. Do not include
secrets or personal data in prompts. A missing key returns a safe error; the
service does not silently fall back. Mocked development and automated tests do
not make live Gemini API calls.

One synthetic live acceptance request completed the drafting and deterministic
planning pipeline with HTTP 200 on 2026-11-27. This verifies that request path
and configured model for that smoke only; unsupported ambiguity paths were
verified offline, and whether alias normalization was needed in the live
response is unknown. See the AI-assisted specification document for details.

From the repository root, start the application with the platform-specific
command. On Windows PowerShell, use the single-process launcher so Psycopg's
async connections run on a per-run `SelectorEventLoop`:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e "backend[dev]"
python backend/run_windows.py
```

The Windows launcher intentionally runs without reload or multiple workers;
the Windows Selector loop does not support asyncio subprocesses. On Linux or
macOS, use Uvicorn's standard loop and development reload:

```bash
python -m uvicorn app.main:app --reload --app-dir backend
```

Open `http://127.0.0.1:8000/docs` for the generated API docs. Available
routes:

- `GET /health`
- `GET /health/database`
- `GET /api/v1/connections/flagsmith/status`
- `GET /api/v1/flagsmith/projects`
- `GET /api/v1/flagsmith/projects/{project_id}/environments`
- `GET /api/v1/flagsmith/environments/{environment_id}`
- `GET /api/v1/flagsmith/environments/{environment_id}/flags`
- `GET /api/v1/flagsmith/projects/{project_id}/flags/{feature_id}`
- `GET /api/v1/experiments/health`
- `POST /api/v1/experiments/validate`
- `POST /api/v1/experiments/plan`
- `POST /api/v1/experiments/plans`
- `POST /api/v1/experiments/plans/{plan_id}/versions`
- `GET /api/v1/experiments/plans?page=1&page_size=20`
- `GET /api/v1/experiments/plans/{plan_id}`
- `GET /api/v1/experiments/plans/{plan_id}/versions?page=1&page_size=20`
- `GET /api/v1/experiments/plans/{plan_id}/versions/{version_number}`
- `POST /api/v1/experiments/draft`
- `POST /api/v1/experiments/drafts`

Example review requests:

```text
GET /api/v1/experiments/plans?page=1&page_size=20
GET /api/v1/experiments/plans/PLAN_UUID
GET /api/v1/experiments/plans/PLAN_UUID/versions?page=1&page_size=20
GET /api/v1/experiments/plans/PLAN_UUID/versions/1
```

The list response contains compact current-version metadata and verifies each
listed current snapshot; it does not fetch historical snapshots. Detail and
exact-version responses include the specification, plan, validation,
provenance, blocking reasons, missing information, and verified integrity
status. Version history is ordered by descending version number and verifies
each returned snapshot. Read endpoints derive ownership from
`LOCAL_OWNER_SUBJECT`; a supplied `owner_subject` query parameter is rejected.

Environment identifiers returned by ExperimentOps are Flagsmith UUIDs;
the upstream environment API key is never returned. The `/flags` endpoint
resolves the environment's project and returns that project's feature
catalog. It does not return identity overrides or evaluated values.

Run mocked tests with `python -m pytest -q backend/tests`. The optional
read-only live check is skipped by default. To enable it, set
`FLAGSMITH_INTEGRATION=1` and `FLAGSMITH_API_KEY` in the environment, then
run `python -m pytest -q backend/tests/integration`.

See [Experiment planning and validation](docs/13-EXPERIMENT-PLANNING-AND-VALIDATION.md)
for request fields, validation rules, readiness states and current limitations.
See [AI-assisted specification drafting](docs/14-AI-ASSISTED-SPECIFICATION.md)
for provider configuration, provenance, request limits, and safety boundaries.

## Official references

-   [Flagsmith: Set up an experiment with a coding
    agent](https://docs.flagsmith.com/experimentation/set-up-with-a-coding-agent)
-   [Flagsmith: Run an
    experiment](https://docs.flagsmith.com/experimentation/run-an-experiment)
-   [Flagsmith: Connect a
    warehouse](https://docs.flagsmith.com/experimentation/connect-a-warehouse)
-   [Flagsmith: Management
    API](https://docs.flagsmith.com/integrating-with-flagsmith/flagsmith-api-overview/management-api)
-   [Flagsmith CLI](https://github.com/Flagsmith/flagsmith-cli)
-   [Flagsmith MCP server](https://github.com/Flagsmith/flagsmith-mcp)

See `docs/06-FLAGSMITH-INTEGRATION.md` for implementation caveats.
Verify current API schemas against the live API explorer before coding;
API versions and beta capabilities can change.
