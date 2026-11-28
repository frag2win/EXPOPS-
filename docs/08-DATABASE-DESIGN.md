# 08 --- Database Design

**Version:** 0.2

> **M4.5 implementation status:** PostgreSQL configuration, async SQLAlchemy
> runtime, Alembic infrastructure, immutable plan versions, read-only review,
> local identity, and immutable approval/rejection decisions with audit records
> are implemented. Experiment execution and Flagsmith writes remain disabled.

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

The optional PostgreSQL integration tests run only when
`EXPOPS_POSTGRES_INTEGRATION=1` and `TEST_DATABASE_URL` are explicitly set. Use
a dedicated local test database; its URL shape is
`postgresql+psycopg://<user>:<password>@localhost:5432/<test_db>`. They upgrade
the schema and verify actual plan creation, hash/provenance retrieval, owner
isolation, trigger rejection of direct snapshot updates/deletes, version
preservation, concurrent allocation, and rollback. The test runner selects
the Selector event loop for database async operations; this does not replace
the separate Windows application launcher check. A skipped integration test
is not evidence of PostgreSQL compatibility.

## 2. Proposed core entities (not implemented)

The following tables remain design targets unless marked implemented.

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

### `experiment_plans` and `experiment_plan_versions` (implemented in M4.2)

`experiment_plans` stores a stable UUID, server-configured `owner_subject`,
the mutable `current_version` pointer, and `created_at`.
`experiment_plan_versions` stores immutable `(plan_id, version)` snapshots:
`specification_json`, `plan_json`, `validation_json`, `provenance_json`,
optional `draft_json`, `content_hash`, `hash_format_version`, `created_at`,
and nullable `legacy_*` fields used only to preserve project/environment IDs,
status, and update timestamp from the prior schema during upgrade.

The version table has a composite primary key and a restrictive foreign key
to its parent. The parent pointer has a deferred composite foreign key to a
version row. A PostgreSQL `BEFORE UPDATE OR DELETE` trigger rejects all
mutations to version rows. Version creation locks the parent row with
`SELECT ... FOR UPDATE`, then advances its pointer in the same transaction.

Hash format 1 canonicalizes specification, plan, validation, provenance, and
the optional complete server-generated draft envelope, then computes SHA-256.
Database identifiers, timestamps, parent pointers, and raw prompts are
excluded. Retrieval recomputes and compares the digest. Format 0 verifies
prior M4.2 records using their original response hash format. Legacy rows
become one plan identity each, preserving their UUID and version number.
Because the previous schema did not record an owner, migrated identities use
the reserved `__legacy_local__` owner marker; repository scoping treats those
rows as belonging to the currently configured single local principal. Thus a
change to `LOCAL_OWNER_SUBJECT` does not strand legacy plans. Provenance is
empty because it was not stored, and the old `updated_at` is retained in
`legacy_updated_at`; the old project/environment IDs and status are also kept
in legacy columns. The upgrade verifies row counts before dropping the
renamed source table. This migration is forward-only because downgrade would
discard owner/provenance data.

`LOCAL_OWNER_SUBJECT` is required server configuration for persisted-plan
operations; the example configuration sets it to `local-development`, but
there is no runtime fallback. Its value is trimmed and must be printable text
of at most 255 characters. Missing or invalid configuration causes protected
operations to fail closed with a sanitized service-unavailable response.
FastAPI resolves one canonical typed principal from server settings through a
shared dependency. Request bodies cannot set identity (unknown fields are
rejected), `owner_subject` query parameters are rejected, and client headers
are never used to resolve identity.

All persisted operations pass this principal explicitly to the persistence
service: creation stores its subject, and plan listing, current retrieval,
version history, exact-version retrieval, and version append are owner-scoped.
Missing and foreign-owned plan identifiers have the same not-found response.
Manual persistence is available at `POST /api/v1/experiments/plans`; a new immutable version can
be appended at `POST /api/v1/experiments/plans/{plan_id}/versions`. Current
and exact-version retrieval are available at `GET
/api/v1/experiments/plans/{plan_id}` and `GET
/api/v1/experiments/plans/{plan_id}/versions/{version_number}`. Server-side
Gemini draft persistence uses `POST /api/v1/experiments/drafts`; the existing
ephemeral `POST /api/v1/experiments/draft` is unchanged. M4.2 itself added no
decision operations; M4.5 now provides the exact-version decision workflow
described below.

M4.3 exposes read-only review operations:

- `GET /api/v1/experiments/plans?page=1&page_size=20` lists plans for the
  configured local principal. Pagination is database-level, ordered by
  `created_at DESC, id DESC`, with a default page size of 20 and maximum of 100.
  The query joins and verifies only each plan's current version; it does not
  load historical snapshots. Each item reports
  `integrity_status: "verified"` only after its current snapshot hash passes.
- `GET /api/v1/experiments/plans/{plan_id}` returns the current review snapshot,
  specification summary, validation readiness and blockers, provenance
  summary, and full stored specification/plan/validation/provenance values.
- `GET /api/v1/experiments/plans/{plan_id}/versions?page=1&page_size=20`
  returns a descending-version history page. Every returned snapshot is
  integrity-verified before its hash, readiness, provenance counts, and
  `integrity_status: "verified"` are returned.
- `GET /api/v1/experiments/plans/{plan_id}/versions/{version_number}` returns
  and verifies that exact historical snapshot, including specification,
  plan, validation, and provenance.

Detail and history operations use the existing versioned canonical SHA-256
verification. A mismatch or unsupported hash format fails with the sanitized
`PLAN_INTEGRITY_FAILED` response; corrupted snapshots are neither repaired nor
served as verified. `owner_subject` query overrides are rejected, and absent
or foreign-owned plan IDs share the same not-found response. No review route
changes plan state or calls the drafting agent.

M4.5 adds server-controlled, exact-version human decisions:

- `POST /api/v1/experiments/plans/{plan_id}/versions/{version_number}/approve`
  reruns deterministic validation against the stored specification and current
  read-only Flagsmith data. It records an approval only when readiness is
  `READY_FOR_REVIEW`, validation has no blockers, and all required external
  prerequisites have nonblank evidence, are independently verified, and are
  `AVAILABLE`. Revalidation expires after 60 seconds; stale results are
  rejected. Unknown or missing readiness fails closed. The built-in warehouse inspector currently reports `UNKNOWN`,
  so approvals are blocked until verified warehouse readiness is available.
- `POST /api/v1/experiments/plans/{plan_id}/versions/{version_number}/reject`
  records a nonblank reason against the exact current version without running
  approval revalidation.
- `GET /api/v1/experiments/plans/{plan_id}/decisions` returns paginated
  decision history; `GET
  /api/v1/experiments/plans/{plan_id}/decisions/{decision_id}` returns one
  decision and its audit record. Both endpoints are owner-scoped and verify
  the stored decision, audit, and plan-version relationship.

`experiment_plan_decisions` stores one decision per `(plan_id, plan_version)`,
the acting local principal, the immutable plan content hash, the rejection
reason when present, a canonical request fingerprint, an opaque idempotency
key, and the complete deterministic revalidation evidence. A repeated request
with the same principal, idempotency key, and payload returns the original
decision. Reusing the key for a different request or submitting a second
decision for a decided version returns a conflict.

`experiment_plan_decision_audit` records the actor, event type, exact version
and content hash, revalidation outcome, rejection reason, and idempotency
reference. Decision and audit rows are inserted in one explicit transaction.
PostgreSQL constraints enforce exact-version and audit references, one decision
per version, idempotency uniqueness, and allowed decision payloads. An
initially deferred constraint trigger requires an audit row before a decision
transaction can commit. Triggers reject `UPDATE` and `DELETE` on both tables.
The application re-verifies the linked plan snapshot hash and audit fields on
decision reads. A canonical audit-record hash covers every audit column and
metadata, and is verified on retrieval. Failed approvals do not create
decision or audit rows.

Approval and rejection do not mutate the plan identity or snapshot, do not
perform execution, and do not write to Flagsmith. `executions` and
`execution_steps` below remain proposed only.

### `executions` (proposed; not implemented)

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

### `execution_steps` (proposed; not implemented)

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

### `audit_events` (proposed; M4.5 uses `experiment_plan_decision_audit`)

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
    EXPERIMENT_PLANS ||--|{ EXPERIMENT_PLAN_VERSIONS : snapshots
    EXPERIMENT_PLAN_VERSIONS ||--o| EXPERIMENT_PLAN_DECISIONS : decided_for
    EXPERIMENT_PLAN_DECISIONS ||--|| EXPERIMENT_PLAN_DECISION_AUDIT : audited_by
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
