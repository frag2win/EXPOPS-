# 08 --- Database Design

**Version:** 0.1

## 1. Design principles

-   Persist plans and execution history, not unnecessary
    user/application data.
-   Store credential references, not plaintext API keys.
-   Keep a durable audit trail.
-   Make approval refer to an immutable plan version/hash.
-   Store external Flagsmith IDs separately from internal IDs.

## 2. Core entities

### `users`

For the hackathon MVP this may be a single local user or a minimal auth
identity. Do not build a full identity platform unless required.

Fields: - `id` UUID primary key - `email` nullable - `created_at`

### `flagsmith_connections`

-   `id` UUID
-   `owner_user_id`
-   `label`
-   `base_url`
-   `credential_reference` (reference to environment secret, not secret
    itself)
-   `created_at`
-   `last_verified_at`
-   `status`

### `flagsmith_contexts`

-   `id` UUID
-   `connection_id`
-   `external_project_id`
-   `project_name`
-   `external_environment_id`
-   `environment_name`
-   `created_at`

### `experiment_plans`

-   `id` UUID
-   `owner_user_id`
-   `connection_id`
-   `project_id`
-   `environment_id`
-   `version` integer
-   `status`
-   `prompt_redacted`
-   `normalized_brief` JSONB
-   `plan_json` JSONB
-   `plan_hash`
-   `validation_json` JSONB
-   `created_at`
-   `updated_at`
-   `expires_at`

### `approvals`

-   `id` UUID
-   `plan_id`
-   `plan_version`
-   `plan_hash`
-   `approved_by`
-   `decision` (`approved` / `rejected`)
-   `reason` nullable
-   `created_at`
-   `expires_at`

### `executions`

-   `id` UUID
-   `plan_id`
-   `approval_id`
-   `status`
-   `idempotency_key`
-   `started_at`
-   `completed_at`
-   `summary`

Unique constraint on `idempotency_key` within the appropriate owner/plan
scope.

### `execution_steps`

-   `id` UUID
-   `execution_id`
-   `step_index`
-   `action_type`
-   `status`
-   `external_resource_type`
-   `external_resource_id`
-   `request_summary_redacted` JSONB
-   `response_summary_redacted` JSONB
-   `verification_status`
-   `error_code`
-   `error_message_redacted`
-   `started_at`
-   `completed_at`

### `audit_events`

-   `id` UUID
-   `actor_user_id`
-   `plan_id`
-   `execution_id` nullable
-   `event_type`
-   `metadata_redacted` JSONB
-   `created_at`

## 3. Relationships

``` mermaid
erDiagram
    USERS ||--o{ FLAGSMITH_CONNECTIONS : owns
    FLAGSMITH_CONNECTIONS ||--o{ FLAGSMITH_CONTEXTS : exposes
    USERS ||--o{ EXPERIMENT_PLANS : creates
    FLAGSMITH_CONNECTIONS ||--o{ EXPERIMENT_PLANS : used_by
    EXPERIMENT_PLANS ||--o{ APPROVALS : receives
    EXPERIMENT_PLANS ||--o{ EXECUTIONS : produces
    APPROVALS ||--o{ EXECUTIONS : authorizes
    EXECUTIONS ||--o{ EXECUTION_STEPS : contains
    EXPERIMENT_PLANS ||--o{ AUDIT_EVENTS : records
```

## 4. Retention

For the hackathon demo: - Keep plan and execution metadata for the
demo. - Do not store raw secrets. - Avoid storing raw user event
streams. - Provide a way to delete local demo data. - Document that
audit records may contain resource names and IDs.

## 5. MVP simplification

If time is extremely limited, use SQLite for local-only development but
keep the repository layer and schema portable to PostgreSQL. Do not
spend the hackathon building a sophisticated multi-tenant database.
