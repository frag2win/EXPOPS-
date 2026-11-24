# 12 --- Hackathon Submission and Demo Plan

**Track:** Track 2 --- AI Agents and Feature Flags\
**Project:** ExperimentOps

## 1. One-line pitch

ExperimentOps is an approval-first AI agent that turns natural-language
experimentation requests into validated, executable Flagsmith workflows,
with verification and an auditable change history.

## 2. Problem

Experiment setup requires developers to translate a product hypothesis
into feature variants, allocation rules, metrics and platform
configuration. A basic prompt-to-configuration workflow can still miss
prerequisites, conflict with existing resources or make changes without
a reviewable operational process.

## 3. Solution

ExperimentOps inspects the connected Flagsmith environment, builds a
structured experiment plan, validates it, requests human approval,
executes supported operations and verifies the resulting state.

## 4. Differentiation

Do not pitch this as "ChatGPT for Flagsmith."

Pitch the safety and lifecycle layer: - read-before-write inspection; -
deterministic preflight validation; - reviewable plan and approval; -
execution through verified Flagsmith operations; - read-after-write
verification; - audit trail and partial-failure recovery; - honest
distinction between configured experiments and event-backed results.

The differentiation must be visible in the demo, not just described in
slides.

## 5. Suggested 3-minute demo

### 0:00--0:25 --- Problem

Show the multi-step nature of setting up an experiment and explain why a
prompt alone is not enough.

### 0:25--0:55 --- Natural-language request

Enter a checkout experiment request with control, variant, allocation
and success event.

### 0:55--1:25 --- Inspection and validation

Show selected project/environment, existing flags, warehouse
prerequisite and validation findings.

### 1:25--1:45 --- Human approval

Show the exact proposed changes and approve them.

### 1:45--2:25 --- Real execution

Show the backend executing against Flagsmith and verifying created
resources.

### 2:25--2:50 --- Results/lifecycle

Show actual experiment status. If no real events have arrived,
explicitly show "waiting for events" rather than inventing results.

### 2:50--3:00 --- Value

Summarize the workflow, safety controls and time saved using measured
timing.

## 6. Evidence to capture

-   Architecture diagram.
-   Short screen recording.
-   Flagsmith dashboard showing created resources.
-   Approval screen.
-   Execution timeline.
-   Tests passing.
-   README setup instructions.
-   Measured manual vs assisted workflow time, if available.

## 7. Claims to avoid

Do not claim: - statistical significance without evidence; - real user
adoption when only test traffic was used; - automatic instrumentation of
arbitrary applications; - full lifecycle support if only creation is
implemented; - production safety without testing; - that Flagsmith
itself lacks capabilities it already provides.

## 8. Submission checklist

-   [ ] Track 2 requirements mapped to implemented features.
-   [ ] Flagsmith integration is real and demonstrated.
-   [ ] Human approval is enforced server-side.
-   [ ] No secrets committed.
-   [ ] Demo uses a non-production environment.
-   [ ] Errors and blockers are shown honestly.
-   [ ] README contains reproducible setup steps.
-   [ ] Repository includes licence and attribution for
    dependencies/assets.
-   [ ] Demo video clearly shows the agent taking actions.
-   [ ] Any test-generated traffic is labelled as such.
