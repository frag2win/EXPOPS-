# 10 --- MVP Roadmap and Task Breakdown

**Version:** 0.1\
**Timebox:** Three-day hackathon implementation plan

> **Status (2026-11-27):** Milestones 1 and 2 and Milestone 3 AI-assisted
> specification drafting are implemented. Persistence, approval workflow,
> Flagsmith writes, result reporting, and frontend remain deferred.

## Guiding rule

Build one complete vertical slice before adding breadth. A working read
→ plan → validate → approve → execute → verify flow is more valuable
than many unfinished agent features.

## Day 0 --- Environment and repository setup

-   [ ] Create GitHub repository.
-   [ ] Add documentation pack.
-   [ ] Create backend and frontend folders.
-   [ ] Add `.gitignore` and `.env.example`.
-   [ ] Create a dedicated Flagsmith test project/environment.
-   [ ] Verify API credential and permissions.
-   [ ] Verify experimentation warehouse prerequisite.
-   [ ] Record the current Flagsmith CLI version and API base URL.
-   [ ] Save sanitized API response fixtures.

**Exit criterion:** Backend can authenticate and perform a read-only
Flagsmith request.

## Day 1 --- Core integration and planning

-   [ ] FastAPI skeleton and health endpoint.
-   [ ] Flagsmith adapter with typed client.
-   [ ] Read projects/environments/flags.
-   [ ] Define ExperimentBrief and ExperimentPlan schemas.
-   [ ] Implement deterministic validator.
-   [x] Implement optional prompt-to-structured-draft flow with provenance.
-   [ ] Implement plan persistence.
-   [ ] Build basic plan preview UI.

**Exit criterion:** A natural-language prompt produces a draft and
deterministic validation without making writes. AI suggestions remain
unconfirmed until the user explicitly confirms them.

## Day 2 --- Approval and execution

-   [ ] Implement plan hash/version.
-   [ ] Implement approval endpoint and UI.
-   [ ] Implement allowlisted executor.
-   [ ] Create/configure a feature and variants in test environment.
-   [ ] Create metric and experiment only after API contract
    verification.
-   [ ] Add read-after-write verification.
-   [ ] Add audit trail and partial-failure reporting.
-   [ ] Add idempotency protection.

**Exit criterion:** Approved plan creates a real configuration in
Flagsmith and verifies it.

## Day 3 --- Lifecycle, testing and demo

-   [ ] Add experiment status/result retrieval if supported.
-   [ ] Add prerequisite/error explanations.
-   [ ] Add result-pending state.
-   [ ] Test failure cases.
-   [ ] Polish UI and execution timeline.
-   [ ] Record a demo using real Flagsmith operations.
-   [ ] Write architecture and setup instructions.
-   [ ] Prepare submission description and screenshots.
-   [ ] Verify no credentials or personal data are committed.

**Exit criterion:** End-to-end demo is repeatable and honest about
event/result limitations.

## Priority tiers

### P0 --- Must ship

-   Flagsmith connection.
-   Read project/environment.
-   Prompt to structured brief.
-   Validation.
-   Approval gate.
-   At least one complete verified write workflow.
-   Audit/execution report.

### P1 --- Strongly preferred

-   Create metric and experiment.
-   Start experiment as a separate approved action.
-   Status/results view.
-   Warehouse preflight.

### P2 --- Stretch

-   Rollback/stop workflow.
-   Experiment history analysis.
-   Natural-language comparison of past experiments.
-   More than one LLM provider.
-   MCP integration, if it adds real value and covers required
    operations.

## Cut list if behind schedule

Cut in this order: 1. Multi-agent orchestration. 2. Advanced charts. 3.
User accounts and multi-tenancy. 4. Automated source-code
instrumentation. 5. MCP support if CLI/API already provides reliable
operations. 6. Advanced statistical recommendations.

Do not cut approval, credential protection, API verification or honest
status reporting.
