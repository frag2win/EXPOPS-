# 03 --- System Architecture

**Version:** 0.1

## 1. Architecture goals

-   Keep the LLM away from raw credentials and unrestricted network
    access.
-   Separate reasoning from deterministic validation and execution.
-   Make every write reviewable and approval-gated.
-   Isolate Flagsmith API/CLI details behind an adapter.
-   Keep the MVP deployable locally.

## 2. High-level architecture

``` mermaid
flowchart TD
    U[Developer] --> UI[React + TypeScript UI]
    UI --> API[FastAPI Backend]
    API --> AUTH[Connection & Project Context]
    API --> ORCH[Agent Orchestrator]
    ORCH --> LLM[LLM Provider]
    ORCH --> TOOLS[Allowlisted Domain Tools]
    TOOLS --> VALID[Deterministic Plan Validator]
    VALID --> PLAN[Plan + Warnings]
    PLAN --> UI
    UI -->|Explicit approval| API
    API --> EXEC[Approval-Gated Executor]
    EXEC --> ADAPTER[Flagsmith Adapter]
    ADAPTER --> FS[Flagsmith Management API / CLI]
    FS --> ADAPTER
    ADAPTER --> VERIFY[Read-after-write Verification]
    VERIFY --> DB[(PostgreSQL)]
    API --> DB
    API --> UI
```

## 3. Component responsibilities

### Frontend

-   Connection status and project/environment selection.
-   Experiment brief composer.
-   Plan preview with changes, warnings and blockers.
-   Approval/rejection controls.
-   Execution progress and audit history.
-   Experiment status/results view.

### FastAPI application

-   Authentication/session handling for ExperimentOps.
-   Request validation.
-   Agent orchestration endpoint.
-   Approval endpoint.
-   Execution endpoint.
-   Audit and persistence.
-   Flagsmith credential boundary.

### Agent orchestrator

-   Converts user intent into a structured experiment brief.
-   Uses read-only tools to inspect context.
-   Requests missing information.
-   Produces a proposed plan.
-   Does not directly execute arbitrary API calls.

### Deterministic validator

-   Validates required fields and constraints.
-   Checks allocation arithmetic.
-   Checks project/environment consistency.
-   Checks event/metric naming.
-   Checks prerequisite results.
-   Produces machine-readable errors and warnings.

### Approval-gated executor

-   Accepts only a stored plan ID and approved plan version/hash.
-   Revalidates before execution.
-   Executes an allowlisted sequence.
-   Persists each step's status.
-   Verifies external state after writes.

### Flagsmith adapter

-   Encapsulates authentication, API paths, CLI calls and response
    parsing.
-   Uses official API schemas and pinned CLI versions.
-   Never exposes credentials to the agent model.

### Database

-   Stores connections metadata (not plaintext secrets), projects,
    plans, approvals, execution steps and audit records.
-   Stores only the minimum information required.

## 4. Trust boundaries

1.  Browser to backend: authenticated application API.
2.  Backend to LLM: send only the minimum context; never send API
    secrets.
3.  Backend to Flagsmith: secret-bearing server-side connection.
4.  Agent output to validator: treat all model output as untrusted.
5.  Approved plan to executor: verify plan hash/version and user
    permission.

## 5. Execution sequence

``` mermaid
sequenceDiagram
    actor Dev as Developer
    participant UI as Frontend
    participant API as FastAPI
    participant Agent as Agent
    participant Val as Validator
    participant FS as Flagsmith
    participant DB as Database

    Dev->>UI: Submit experiment prompt
    UI->>API: POST /plans
    API->>Agent: Parse intent
    Agent->>FS: Read project/environment/flags
    FS-->>Agent: Current state
    Agent-->>API: Structured proposal
    API->>Val: Validate proposal and prerequisites
    Val-->>API: Errors/warnings/ready status
    API->>DB: Persist plan
    API-->>UI: Plan preview
    Dev->>UI: Approve exact plan
    UI->>API: POST /plans/{id}/approve
    API->>DB: Record approval and plan hash
    API->>FS: Execute allowlisted operations
    FS-->>API: Operation responses
    API->>FS: Read-after-write verification
    FS-->>API: Verified state
    API->>DB: Persist execution and audit
    API-->>UI: Final execution report
```

## 6. Deployment topology for MVP

Local development: - Frontend dev server. - FastAPI backend. -
PostgreSQL via Docker Compose. - Flagsmith Cloud test project or a local
Flagsmith instance. - LLM API configured through server-side environment
variables.

Do not self-host Flagsmith for the first vertical slice unless required
by the chosen track or environment. First prove integration against a
dedicated non-production Flagsmith project.

## 7. Architecture decisions

-   Start with a single orchestrator, not a multi-agent swarm.
-   Use deterministic domain tools for all external operations.
-   Use a Flagsmith adapter so REST/CLI details do not leak into
    business logic.
-   Prefer Management API for supported management operations; use CLI
    where it is the documented/verified path or provides useful
    authentication/context.
-   Do not assume the MCP server supports every experimentation
    lifecycle operation. Verify each required capability before choosing
    MCP as the execution path.
