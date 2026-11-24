# 06 --- Flagsmith Integration Notes

**Version:** 0.1\
**Status:** Requires API contract verification before implementation

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
