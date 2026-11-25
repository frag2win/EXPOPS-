# 06 --- Flagsmith Integration Notes

**Version:** 0.1\
**Status:** Read-only adapter and sandbox connectivity verified (2026-11-25)

## 1. Official documentation reviewed

-   [Set up an experiment with a coding
    agent](https://docs.flagsmith.com/experimentation/set-up-with-a-coding-agent)
-   [Run an
    experiment](https://docs.flagsmith.com/experimentation/run-an-experiment)
-   [Connect a
    warehouse](https://docs.flagsmith.com/experimentation/connect-a-warehouse)
-   [Management
    API](https://docs.flagsmith.com/integrating-with-flagsmith/flagsmith-api-overview/management-api)
-   [Flagsmith CLI
    repository](https://github.com/Flagsmith/flagsmith-cli)
-   [Flagsmith MCP server](https://github.com/Flagsmith/flagsmith-mcp)

## 2. Important current constraints

The official experimentation documentation currently labels
experimentation as **Beta**.

The documented coding-agent setup flow requires: - a Flagsmith instance
with a connected warehouse; - a supported SDK capable of sending
experimentation events; - Flagsmith CLI v2 or later; - an application
identity strategy that uses the same identity for exposure and
conversion events.

The documentation lists supported SDK minimums; verify them at
implementation time because versions can change: - JavaScript/React:
`@flagsmith/flagsmith` 12.4.0+ - Python: `flagsmith` 6.3.0+ - Java:
`com.flagsmith:flagsmith-java-client` 8.2.0+ - Node.js:
`@flagsmith/nodejs` 9.1.0+ - Go: `flagsmith-go-client/v5` 5.3.0+ -
Flutter: `flagsmith` 6.2.0+

Do not assume that configuring a flag automatically instruments an
application. Exposure and conversion events must be emitted by a
compatible SDK/application.

## 3. Integration strategy

### Recommended initial approach

1.  Use Flagsmith Management API for supported management operations.
2.  Use the Flagsmith CLI v2 where its documented commands/API wrapper
    are needed.
3.  Build a narrow adapter that exposes only the operations required by
    the MVP.
4.  Verify every endpoint and request schema against the live API
    explorer/OpenAPI schema before implementing it.
5.  Do not rely on an MCP server for experiment creation/start until
    that exact capability is confirmed.

The official coding-agent prompt currently uses the CLI's
`flagsmith api <path>` for experiment metric and experiment endpoints,
and explicitly says not to use the Flagsmith MCP server for starting
experiments because it cannot start them in that documented workflow.
The MCP server's available capabilities can evolve; verify rather than
generalize.

## 4. Experiment setup dependencies

A typical documented experiment setup involves: 1. A multivariate
feature flag with keyed variations. 2. A metric that references an event
name. 3. An experiment that references the feature and metric. 4. SDK
instrumentation that records exposures and conversion events. 5.
Starting the experiment. 6. Refreshing/reading results when events have
arrived.

The exact API request fields must be taken from the current
documentation/API schema. Do not hard-code the sample payload in the
product domain layer.

## 5. Warehouse prerequisite

Before attempting experiment creation, check whether the selected
environment has a warehouse connection.

If no warehouse is configured: - mark the plan `BLOCKED`; - explain that
experimentation results require the relevant warehouse setup; - link to
[Connect a
warehouse](https://docs.flagsmith.com/experimentation/connect-a-warehouse); -
do not pretend that the experiment can produce valid results.

## 6. Event instrumentation facts

-   Exposures and conversions are joined per identity.
-   Use the same stable identity for exposure and conversion events.
-   Event names are case-sensitive and must match the metric definition.
-   SDK events may be buffered; flush/close correctly before a
    short-lived process exits.
-   A test harness can send test events only when clearly labelled as
    test traffic and isolated from production.
-   A configuration-only demo must say that real results are pending
    until genuine events arrive.

## 7. CLI notes

The current Flagsmith CLI repository documents: - installation using the
official installer or Homebrew; - `flagsmith init`; -
project/environment commands; - feature and variant commands; -
`flagsmith api <path>`; - machine-readable JSON output.

Pin the CLI version used by the project and verify the exact command
names and flags with `flagsmith --help` and the official CLI reference.

## 8. MCP notes

Flagsmith has an MCP server. However: - capability coverage may not
match every experimentation lifecycle operation; - the official
coding-agent setup documentation currently instructs users not to use
the MCP server for the experiment-start step; - do not assume MCP is the
only integration path.

For the MVP, use a verified Management API/CLI adapter for writes. MCP
can be investigated as an optional read/discovery interface later.

## 9. Credential handling

-   Use a dedicated non-production Flagsmith project/environment.
-   Use the least-privileged credential that can perform required
    operations.
-   Keep the credential only on the backend.
-   Never commit `flagsmith.env`, API keys or environment keys.
-   Redact all credentials from logs and error traces.
-   Provide a disconnect/revoke procedure in future versions.

## 10. Contract verification checklist

Before writing the adapter: - \[ \] Confirm base URL and API version. -
\[ \] Confirm authentication header and credential type. - \[ \] Confirm
project and environment list endpoints. - \[ \] Confirm feature and
variant create/read endpoints. - \[ \] Confirm metric create/read
endpoints. - \[ \] Confirm experiment create/read/start/stop
endpoints. - \[ \] Confirm result refresh/read endpoints. - \[ \]
Confirm warehouse connection endpoint and response. - \[ \] Confirm rate
limits and retry semantics. - \[ \] Confirm API permissions needed. - \[
\] Save sanitized request/response fixtures for contract tests.

## 11. Source links

-   [Experiment setup with coding
    agent](https://docs.flagsmith.com/experimentation/set-up-with-a-coding-agent)
-   [Management API
    overview](https://docs.flagsmith.com/integrating-with-flagsmith/flagsmith-api-overview/management-api)
-   [CLI repository](https://github.com/Flagsmith/flagsmith-cli)
-   [MCP server repository](https://github.com/Flagsmith/flagsmith-mcp)

## 12. Milestone 1 contract verification (2026-11-24)

The current Management API OpenAPI document was inspected at
`https://api.flagsmith.com/api/v1/swagger.json`. The verified Cloud base
URL is `https://api.flagsmith.com/`. Management API credentials use
`Authorization: Api-Key <token>`; an API token's effective access follows
the organisation role/permissions attached to it. See the official
[authentication documentation](https://docs.flagsmith.com/integrating-with-flagsmith/flagsmith-api-overview/management-api/authentication).

Read routes present in the current schema:

- `GET /api/v1/projects/`
- `GET /api/v1/projects/{id}/environments/`
- `GET /api/v1/environments/get-by-uuid/{uuid}/`
- `GET /api/v1/projects/{project_pk}/features/`
- `GET /api/v1/projects/{project_pk}/features/{id}/`

The published schema's response reference for project environments points
to `ProjectList`, whose declared fields do not describe environments. The
adapter validates required fields and fails closed on an unexpected
payload. This response-shape inconsistency still needs confirmation
against an authenticated non-production account.

Flagsmith environment detail responses include an `api_key`; ExperimentOps
uses the environment UUID for internal routes and drops the API key from
all response models. Project feature catalog reads are not the same as
environment evaluated state or identity overrides. This milestone does
not claim to provide those values.

The live schema was reachable without credentials, but no valid user
credential was available during implementation. Authentication, account
permissions, real response payloads and a live environment discovery
therefore remain unverified. No frontend has been started pending a live
backend integration check.

## 13. Live integration verification attempt (2026-11-24)

The application settings resolved the default Cloud URL to
`https://api.flagsmith.com` and the configured timeout to 10 seconds. No
`FLAGSMITH_API_KEY` was present in the process environment or repository
`.env`. In accordance with the verification procedure, no authenticated
Flagsmith request was sent. No live authentication, project, environment
or feature response shape was observed during this attempt, and the
project-environments response reference remains unresolved against a
real account.

Code review confirms that the client sends `Authorization: Api-Key <key>`
from a Pydantic `SecretStr` setting, constructs only GET requests, uses a
bounded HTTPX timeout, and returns sanitized typed errors. Each HTTPX
client is scoped to an async context manager; the adapter's `close()` is
currently a no-op because it retains no client connection pool. Error
mapping for invalid credentials, permission denial, missing resources,
timeouts, invalid payloads and server failures is covered by mocked tests,
not by live responses. The full test suite for this verification attempt
passed 18 tests; the opt-in live test was skipped.

## 14. Live Integration Verification — 2026-11-25

### Live checks

No `FLAGSMITH_API_KEY` was configured in the process environment or the
repository-root `.env`. The configured base URL was
`https://api.flagsmith.com`, and the timeout was 10 seconds. No
authenticated request was sent.

| Check | Result |
| --- | --- |
| Authentication and connection status | Not attempted: credential missing |
| Project discovery | Not attempted |
| Project environment discovery | Not attempted |
| Environment detail by UUID | Not attempted |
| Project feature catalog | Not attempted |
| Actual authenticated response structures | None observed |

The project-environments OpenAPI response reference remains unresolved
against a real account. The previously observed schema reference and
mocked fixtures are not live response evidence. No sanitized live fixture
was added.

### Security review and error coverage

Code review confirms the API key setting is `SecretStr`, the client sends
it only in the `Authorization: Api-Key …` header, all adapter requests
are GET requests, and the timeout is bounded. `.env` is ignored by Git.
The HTTPX client is opened and closed within each request context; the
application shutdown hook calls the adapter's no-op `close()` because no
persistent client pool is retained. Existing mocked tests cover missing
credentials, 401, 403, 404, 429, 5xx, timeouts and unexpected payloads
without returning upstream response bodies or headers. Those scenarios
were not exercised against Flagsmith during this verification.

### Automated suite

`python -m pytest -q` from `backend/`: 18 passed, 0 failed, 1 skipped.
The skipped test is the opt-in live integration test. This was the state
at the time of that earlier attempt; the successful live verification is
recorded in Section 15 below.

## 15. Live Flagsmith Verification — 2026-11-25

### Configuration and authenticated reads

The backend loaded a non-placeholder `FLAGSMITH_API_KEY` as `SecretStr`
from the repository-root `.env`. The resolved base URL was
`https://api.flagsmith.com`, the request timeout was 10 seconds, and the
client's authorization scheme was `Api-Key`. The real connection-status
route returned HTTP 200 with `configured=true` and `authenticated=true`.

All discovery was read-only:

| Operation | Live result |
| --- | --- |
| Project list | Array response; one accessible project; `ExperimentOps Demo` found |
| Project environments | Array response with two environments; `Development` found |
| Environment detail by UUID | Read succeeded; UUID and project association validated |
| Project feature catalog | Paginated object (`count`, `next`, `previous`, `results`); one feature |
| Target feature | `checkout_simplified` found |
| Unknown environment UUID | HTTP 404 mapped to `FLAGSMITH_RESOURCE_NOT_FOUND` with sanitized message |

No project IDs, environment UUIDs, environment API keys or credential
values are recorded here.

### Environment response contract

The authenticated `GET /api/v1/projects/{id}/environments/` response was
an array of environment objects. The observed objects include `id`,
`uuid`, `name`, `project`, environment metadata and an `api_key` field.
The environment detail endpoint returns an object with the same core
identifier/name/project fields and also includes `api_key`.

This live response differs from the published OpenAPI reference to
`ProjectList`; that reference is incorrect for the actual response. The
existing adapter already accepts an array and maps `uuid`, `name` and
`project` into the internal schema, so no adapter change was justified.
The paginated feature response was also accepted through the existing
adapter.

A regression fixture derived from the live environment shape is stored at
`backend/tests/fixtures/flagsmith_project_environments.sanitized.json`.
Its identifiers and metadata values are sanitized; the target environment
name is retained, and the `api_key` field is omitted. The fixture test
confirms the array/object structure and adapter mapping without relaxing
validation.

### Security, error handling and tests

Source review confirms all outbound Flagsmith operations use HTTP GET,
the HTTPX timeout is bounded at 10 seconds, and each HTTPX client is
closed by its request context manager. The API key is not returned by
internal schemas or error responses. Error handling tests cover missing
credentials, 401, 403, 404, 429, 5xx, timeout and unexpected response
cases with mocked responses; the live unknown-environment 404 also
confirmed sanitized error mapping. Invalid credentials and insufficient
permissions were not tested against the live account because doing so
would require using a different credential or changing permissions.

With `FLAGSMITH_INTEGRATION=1`, the complete suite ran against the
configured environment: **20 passed, 0 failed, 0 skipped**, including one
live Flagsmith discovery test and the mocked/configuration tests. Python
compilation also succeeded. No write or mutation request was made.

The Milestone 1 read-only connectivity acceptance criteria are met for
the configured sandbox. Experiment planning, approval and writes remain
outside this milestone's verified scope.
