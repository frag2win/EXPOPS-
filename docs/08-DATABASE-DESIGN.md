# 08 --- Database Design

**Version:** 0.1

> **M4.1 implementation status (2026-11-28):** PostgreSQL configuration,
> async SQLAlchemy runtime, safe database readiness, and Alembic infrastructure
> are implemented. The baseline migration is empty. Plan, approval, execution,
> and audit tables below remain proposed and have not been created.

## 1. Design principles

-   Persist plans and execution history, not unnecessary
    user/application data.
-   Store credential references, not plaintext API keys.
-   Keep a durable audit trail.
-   Make approval refer to an immutable plan version/hash.
-   Store external Flagsmith IDs separately from internal IDs.

## 1.1 Implemented M4.1 infrastructure

`DATABASE_URL` is optional and loaded as a Pydantic `SecretStr`. The only
accepted URL scheme is `postgresql+psycopg`; the connection string is not
returned by health checks or database error handling. The FastAPI lifespan
creates a pooled async SQLAlchemy engine only when configured and disposes it
on shutdown. Existing application endpoints do not depend on database
connectivity.

Psycopg 3's asynchronous API is incompatible with the default Windows
`ProactorEventLoop`. On Windows, start the API from the repository root with
`python backend/run_windows.py`. The launcher creates a Selector loop only for
the server run and does not alter the process-wide loop policy. It runs one
worker without reload because Windows Selector loops do not support asyncio
subprocesses. Online Alembic commands and the opt-in PostgreSQL test select the
same loop explicitly for their own runs. Linux and macOS keep their standard
event-loop behavior.

The database readiness endpoint is `GET /health/database`. It executes `SELECT
1` and reports `ready`, `unavailable`, or `not_configured`. The last state has
`reachable: null` because no connection check was possible. The existing
`GET /health` remains an application health check and does not imply database
readiness.

Alembic is configured under `backend/alembic.ini`; its initial `m4_1_empty`
revision creates no business tables. `Base.metadata` is empty at this stage.
Generate and apply migrations from `backend/` after setting `DATABASE_URL` in
the ignored repository-root `.env`:

```powershell
python -m pip install -e ".[dev]"
alembic -c alembic.ini revision --autogenerate -m "describe schema change"
alembic -c alembic.ini upgrade head
alembic -c alembic.ini current
python -m pytest -q tests
```

The repository-root `.env.example` shows the supported URL format with
explicit placeholders. URL-encode special characters in local credentials.
Create a local PostgreSQL role and database with the local PostgreSQL
administration tools, for example in `psql` as a local administrator:

```sql
CREATE ROLE experimentops LOGIN PASSWORD '<local-password>';
CREATE DATABASE experimentops OWNER experimentops;
```

Put the corresponding URL only in the ignored repository-root `.env`. Do not
place real credentials in the template or documentation. The database health
check does not create tables or run migrations.

The optional PostgreSQL integration test runs only when
`EXPOPS_POSTGRES_INTEGRATION=1` and `TEST_DATABASE_URL` are explicitly set. Use
a dedicated local test database; its URL shape is
`postgresql+psycopg://<user>:<password>@localhost:5432/<test_db>`. The test
checks connectivity and transaction rollback using a temporary table, then
disposes the engine. It creates no persistent application tables. The test
runner selects a compatible event loop for the test itself; this does not
replace the separate Windows application launcher check.

## 2. Proposed core entities (not implemented)

The following tables remain design targets. M4.1 creates none of them.

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
