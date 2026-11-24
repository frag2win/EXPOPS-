# 11 --- Testing Strategy

**Version:** 0.1

## 1. Testing goals

Prove that ExperimentOps: - understands and validates a complete
experiment brief; - refuses incomplete or invalid plans; - does not
write before approval; - uses the selected Flagsmith
project/environment; - performs real supported operations; - verifies
external state; - reports failures honestly; - protects credentials.

## 2. Test layers

### Unit tests

-   Experiment brief parsing schema.
-   Allocation validation.
-   Unique variant keys.
-   Metric/event name validation.
-   Plan hashing and versioning.
-   Approval expiry and hash matching.
-   State transition rules.
-   Secret redaction.

### Adapter contract tests

Use sanitized fixtures from the current Flagsmith API schema: - project
listing; - environment listing; - feature read/create; - variant
read/create; - metric create/read; - experiment create/read/start; -
result refresh/read.

Do not assume endpoints or response shapes. Update fixtures when the
upstream API version changes.

### Integration tests

Against a dedicated non-production Flagsmith project: - authenticate; -
read selected project/environment; - inspect flags; - create a test
feature/variants; - create metric/experiment if prerequisites are
available; - verify resource state; - clean up only through an explicit
test cleanup process.

### Frontend tests

-   Plan preview renders all changes.
-   Warnings and blockers are visible.
-   Approval is disabled for invalid plans.
-   Execution progress is understandable.
-   Result-pending state is not shown as a success metric.

## 3. Required negative tests

-   Missing hypothesis.
-   Missing metric event name.
-   Duplicate variant keys.
-   Invalid allocation total.
-   Wrong environment.
-   Missing warehouse.
-   Invalid API credential.
-   Insufficient API permissions.
-   Rate limit response.
-   API timeout.
-   Partial failure after feature creation.
-   Plan changed after approval.
-   Duplicate execute request.
-   Prompt injection embedded in a feature description.
-   Attempt to write to a blocked production environment.
-   No experiment events available.

## 4. Demo acceptance test

1.  Connect to the test Flagsmith project.
2.  Select a non-production environment.
3.  Submit a natural-language experiment request.
4.  Inspect the generated plan.
5.  Demonstrate at least one validation warning or correction.
6.  Approve the plan.
7.  Execute the real API operations.
8.  Read resources back from Flagsmith.
9.  Show the audit/execution timeline.
10. Explain whether the experiment is configured, running or awaiting
    real events.

## 5. Test data policy

Never use fabricated metrics in the final demo as if they were real. A
local test harness may generate test events only if: - it uses unique
test identities; - it is isolated from production; - it is clearly
labelled; - the presentation discloses that the traffic is
test-generated.

## 6. Quality gate

A release candidate must pass: - all unit tests; - adapter contract
tests; - one successful non-production integration test; -
approval-bypass negative test; - secret-leak scan; - manual end-to-end
demo rehearsal.
