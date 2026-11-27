# 02 --- Software Requirements Specification (SRS)

**Version:** 0.1\
**Status:** Baseline specification; Milestones 1 and 2 implement a subset

> **Implementation status (2026-11-25):** Milestone 2 implements the
> deterministic, ephemeral planning and validation scope. Persistence, LLM
> extraction, approvals and execution remain future work.

## 1. System overview

ExperimentOps is a web application with a backend agent service. The
frontend collects a request and displays plans, approvals, execution
logs and results. The backend uses deterministic tools to inspect and
manage Flagsmith resources.

## 2. Functional requirements

  -----------------------------------------------------------------------
  ID                      Requirement             Priority
  ----------------------- ----------------------- -----------------------
  FR-001                  User can configure a    Must
                          Flagsmith connection    
                          securely                

  FR-002                  User can select a       Must
                          project and environment 

  FR-003                  System can inspect      Must
                          existing flags and      
                          environment metadata    

  FR-004                  User can submit an      Must
                          experiment brief in     
                          natural language        

  FR-005                  Agent extracts          Must
                          hypothesis, control,    
                          variants, allocation    
                          and metric              

  FR-006                  Agent asks for missing  Must
                          mandatory information   

  FR-007                  System generates a      Must
                          structured plan before  
                          writes                  

  FR-008                  Validator checks plan   Must
                          completeness and        
                          consistency             

  FR-009                  System checks           Must
                          experimentation         
                          prerequisites,          
                          including warehouse     
                          readiness               

  FR-010                  User must explicitly    Must
                          approve a specific plan 
                          version before          
                          execution               

  FR-011                  Backend executes only   Must
                          allowlisted Flagsmith   
                          operations              

  FR-012                  Backend verifies        Must
                          created/updated         
                          resources by reading    
                          them back               

  FR-013                  System records action   Must
                          results and errors      

  FR-014                  User can inspect        Should
                          experiment status and   
                          available results       

  FR-015                  System can refresh      Should
                          results when the API    
                          supports it and rate    
                          limits permit           

  FR-016                  User can cancel a       Should
                          pending plan            

  FR-017                  System can stop an      Could
                          experiment through a    
                          supported operation     

  FR-018                  System can export a     Could
                          human-readable          
                          execution report        
  -----------------------------------------------------------------------

## 3. Non-functional requirements

### Security

-   Secrets are stored server-side and never returned to the browser.
-   Logs redact tokens and authorization headers.
-   Writes require approval.
-   Production environment writes are disabled by default in the MVP.
-   Tool calls are allowlisted and schema-validated.

### Reliability

-   External API timeouts are bounded.
-   Retries are limited to safe/idempotent reads or operations with
    idempotency protection.
-   A partial failure is reported with completed and incomplete steps.
-   The system must not claim an operation succeeded until the external
    API confirms it and read-after-write verification passes.

### Usability

-   A developer can understand the plan before approving it.
-   Errors include the failed step, reason and next action.
-   Technical identifiers are visible but accompanied by plain-language
    descriptions.

### Maintainability

-   Flagsmith access is isolated behind an adapter.
-   LLM provider is isolated behind an interface.
-   Validation rules are deterministic and unit tested.
-   API schemas are versioned.

## 4. Core user flow

1.  Configure Flagsmith connection.
2.  Select project and environment.
3.  Submit experiment request.
4.  Agent parses brief.
5.  If required information is missing, ask a focused follow-up
    question.
6.  Inspect current Flagsmith state.
7.  Generate a plan.
8.  Validate plan and prerequisites.
9.  Display preview and warnings.
10. User approves the exact plan hash/version.
11. Execute sequentially.
12. Verify each resource.
13. Display final status and next steps.

## 5. Experiment brief schema

Minimum logical fields:

``` json
{
  "name": "Checkout simplification",
  "hypothesis": "A simplified checkout increases completed purchases",
  "project_id": "selected-project",
  "environment_id": "selected-environment",
  "feature_name": "checkout_simplified",
  "control": {
    "key": "control",
    "description": "Existing checkout"
  },
  "variants": [
    {
      "key": "simplified",
      "description": "Simplified checkout",
      "allocation_percent": 30
    }
  ],
  "success_metric": {
    "name": "Successful purchases",
    "event_name": "purchase_completed",
    "aggregation": "occurrence",
    "direction": "up"
  },
  "rollout_percent": 100
}
```

This is an internal domain model, not a promise that the exact JSON maps
directly to Flagsmith's API. The adapter translates it to the verified
API schema.

## 6. Validation rules

-   Experiment name must be non-empty.
-   Hypothesis must be non-empty and testable.
-   At least one variation beyond control is required for an A/B
    experiment.
-   Variant keys must be unique and stable.
-   Allocations must be valid percentages and sum to the configured
    total according to the Flagsmith experiment model.
-   Metric event name must be exact and case-sensitive.
-   Same identity must be used when recording exposure and conversion
    events in the application.
-   Environment must be explicitly selected.
-   Warehouse readiness must be checked before attempting experiment
    creation.
-   Unsupported or unknown fields must cause a validation error, not be
    silently dropped.
-   If a requested metric cannot be configured, the agent must ask or
    stop.

## 7. State model

``` text
DRAFT
  -> INSPECTING
  -> NEEDS_INPUT | BLOCKED | READY_FOR_APPROVAL
  -> APPROVED
  -> EXECUTING
  -> VERIFYING
  -> CONFIGURED | PARTIALLY_FAILED | FAILED

CONFIGURED
  -> RUNNING (only after supported start action and verification)
  -> COMPLETED / STOPPED (as reported by Flagsmith)
```

`CONFIGURED` does not mean `RUNNING`. `RUNNING` does not mean results
are statistically conclusive.

## 8. Acceptance criteria

-   In a test Flagsmith environment, a complete prompt produces a
    structured plan.
-   A missing metric event name causes a clarification or a blocked
    plan.
-   Invalid allocations are rejected before API writes.
-   A plan cannot execute without approval of that exact plan version.
-   A rejected plan results in zero write calls.
-   A successful execution creates resources and verifies them.
-   A simulated API failure is surfaced without falsely reporting
    success.
-   The audit log records each action without storing secrets.
