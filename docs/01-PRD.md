# 01 --- Product Requirements Document (PRD)

**Product:** ExperimentOps\
**Version:** 0.1\
**Status:** Proposed MVP

## 1. Product vision

Help developers plan, validate, execute and inspect Flagsmith
experiments through a natural-language, approval-first workflow.

ExperimentOps should reduce configuration friction and prevent avoidable
mistakes. It should not claim to replace product judgment, application
instrumentation, or statistical expertise.

## 2. Problem statement

Setting up an experiment can involve several dependent tasks: -
understanding the desired product change and hypothesis; - choosing a
control and one or more variations; - deciding traffic allocation; -
defining a measurable success event and metric; - checking the Flagsmith
project and environment; - confirming experimentation prerequisites; -
creating the required flag, variants, metric and experiment; - ensuring
the application sends exposure and conversion events; - starting,
monitoring and eventually stopping the experiment.

A natural-language request often omits information required to do this
safely. A useful tool must discover missing information, inspect the
current state, validate the plan, and ask for approval before writes.

## 3. Target users

### Primary: software developer / technical product engineer

-   Owns a web or backend application.
-   Uses feature flags or is adopting them.
-   Wants to set up experiments without manually coordinating every
    configuration step.
-   Understands that application code must emit the correct exposure and
    conversion events.

### Secondary: small product team

-   Needs a reviewable history of experiment changes.
-   Wants guardrails around environment selection and production writes.
-   Needs a clear summary of what was configured and what remains to be
    instrumented.

## 4. Jobs to be done

1.  "Help me turn this product question into a measurable experiment."
2.  "Check whether my Flagsmith environment is ready."
3.  "Show me exactly what you intend to create or change."
4.  "Execute only the changes I approve."
5.  "Tell me whether the experiment is running and whether results are
    available."
6.  "Explain what the observed results mean without overstating
    certainty."

## 5. MVP value proposition

A developer can move from a plain-language experiment brief to a
validated, approved and verifiably configured Flagsmith experiment, with
a transparent record of every action.

## 6. MVP scope

### In scope

-   Connect one Flagsmith account / organisation context using a
    server-side credential.
-   Select one project and one environment.
-   Read current flags and relevant environment configuration.
-   Collect experiment brief fields.
-   Produce structured experiment plan.
-   Validate required fields, allocation totals, metric/event naming and
    prerequisites.
-   Detect missing warehouse connection and stop with clear setup
    guidance.
-   Require explicit approval before any write operation.
-   Create or configure the supported flag/variants, metric and
    experiment using verified API/CLI operations.
-   Verify writes by reading the created resources back.
-   Display execution status, errors and experiment link.
-   Read/refresh results where supported by the connected Flagsmith
    setup.
-   Maintain an audit log.

### Explicitly out of scope for MVP

-   Editing arbitrary user application source code.
-   Automatically instrumenting any arbitrary codebase.
-   Building a game or acquiring a public user base.
-   Generating fake production results.
-   Autonomous production rollout without approval.
-   Full multi-agent architecture.
-   Statistical claims beyond what Flagsmith's returned results support.
-   Multi-tenant SaaS billing and organisation management.
-   Supporting every Flagsmith API endpoint.

## 7. Differentiation

Flagsmith already documents a coding-agent workflow for setting up an
experiment in an application's repository. ExperimentOps must not merely
reproduce that prompt.

Our differentiating focus: - **Management-plane workflow:** manage the
experiment configuration as a reviewable operational process. -
**Preflight validation:** inspect prerequisites and existing state
before writing. - **Approval and auditability:** show a change plan,
require approval, record who approved and verify the outcome. -
**Lifecycle visibility:** present the current state, blockers and next
actions in one place. - **Safe failure:** stop on ambiguous or
unsupported operations instead of guessing.

A stretch differentiator is a configuration safety report that
identifies allocation errors, missing metric/event alignment, missing
warehouse prerequisites, duplicate names and unsafe environment
selection.

## 8. Success metrics

### Product / demo metrics

-   A complete happy-path experiment can be created from a prompt.
-   Every write action is preceded by explicit approval.
-   Every successful write is verified by a read-after-write check.
-   Invalid or incomplete plans are blocked with actionable messages.
-   No credentials appear in logs, prompts or frontend payloads.
-   The demo can explain the distinction between configuration success
    and real event/result availability.

### Time-savings claim

Measure a manual baseline and the ExperimentOps workflow using the same
task. Report actual observed times in the demo; do not invent a
percentage.

## 9. Assumptions and risks

-   Flagsmith experimentation is currently documented as beta; verify
    the current status before the demo.
-   Experimentation requires a compatible Flagsmith setup and connected
    warehouse.
-   Supported SDKs and versions are required for exposure/conversion
    events.
-   API schemas and CLI commands may change; pin/test the version used.
-   The LLM may produce invalid or unsafe plans; deterministic
    validation and approval are mandatory.
-   A successful configuration does not guarantee enough traffic or
    statistically meaningful results.

## 10. Product principles

1.  Read before write.
2.  Ask rather than assume.
3.  Never invent IDs, API fields, experiment results or success metrics.
4.  Human approval before mutations.
5.  Least privilege and non-production-first.
6.  Verify every external write.
7.  Make uncertainty visible.
8.  Keep the first version small and demonstrably useful.
