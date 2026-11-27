# ExperimentOps

**Long-term product vision:** an AI-assisted, approval-first control plane
for managing Flagsmith experiments. Current Milestone 2 uses no LLM.

The long-term goal is to turn a developer's intent into a validated,
reviewable plan and support approved management-plane operations through
Flagsmith. Current Milestone 2 stops at planning and validation.

> **Project status:** Milestone 1 read-only integration and Milestone 2
> deterministic planning/validation are implemented. Planning is ephemeral;
> no approval workflow, Flagsmith writes, execution, LLM, database, or frontend
> are implemented.

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
prerequisite status. A `READY_FOR_REVIEW` plan is not approved or executed.
Warehouse readiness and SDK instrumentation are unknown unless supplied as
operator evidence; the backend does not independently verify either.

## Run the backend

Requirements: Python 3.12 or newer. Copy `.env.example` to `.env` in the
repository root and set `FLAGSMITH_API_KEY` to an organization Management
API token. Keep the token server-side; the API never returns it. The
default Cloud URL is `https://api.flagsmith.com`; set
`FLAGSMITH_API_URL` for a self-hosted instance.

The template's `DATABASE_URL` uses the PostgreSQL psycopg driver and explicit
placeholders. If configuring a local PostgreSQL database for development,
replace those placeholders only in the ignored repository-root `.env` file.
The current backend does not consume `DATABASE_URL` or connect to a database;
database persistence is not implemented. Never put database credentials in
`.env.example` or documentation.

From the repository root in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e "backend[dev]"
python -m uvicorn app.main:app --reload --app-dir backend
```

Open `http://127.0.0.1:8000/docs` for the generated API docs. Available
routes:

- `GET /health`
- `GET /api/v1/connections/flagsmith/status`
- `GET /api/v1/flagsmith/projects`
- `GET /api/v1/flagsmith/projects/{project_id}/environments`
- `GET /api/v1/flagsmith/environments/{environment_id}`
- `GET /api/v1/flagsmith/environments/{environment_id}/flags`
- `GET /api/v1/flagsmith/projects/{project_id}/flags/{feature_id}`
- `GET /api/v1/experiments/health`
- `POST /api/v1/experiments/validate`
- `POST /api/v1/experiments/plan`

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
