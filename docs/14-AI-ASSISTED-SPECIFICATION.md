# AI-Assisted Experiment Specification Drafting

**Status (2026-11-27):** Milestone 3 implementation. This feature drafts a
candidate specification; it does not approve, persist, or execute an experiment.

## Request and response

`POST /api/v1/experiments/draft` accepts a JSON body:

```json
{
  "request": "Compare the current checkout with a simpler flow to improve completed purchases.",
  "constraints": ["Keep the existing control unchanged"],
  "provided_specification": {},
  "confirmed_specification": {}
}
```

`request` is required (1–8,000 characters), `constraints` is optional (up to
20 entries, each up to 500 characters), and either structured specification
overlay may be omitted. Unknown fields are rejected. The HTTP body is limited
to 32 KiB. `provided_specification` carries user-supplied fields;
`confirmed_specification` carries explicit confirmations from the user. When
the two contain different values, the confirmed value is used and a conflict
plus a clarification question is returned.

The response includes:

- normalized `specification`, using the Milestone 2 Pydantic model;
- per-field `provenance` (`USER_PROVIDED`, `AI_INFERRED`, `USER_CONFIRMED`,
  or `UNKNOWN`);
- `suggested_values`, which require confirmation and are not applied to the
  normalized specification;
- `missing_information`, `clarification_questions`, `conflicts`, and
  `ambiguities`;
- `planning`, the deterministic plan and validation report;
- provider/model/timing/token metadata and `execution_performed: false`.

Example request text is not evidence for external facts. Project/environment
resolution occurs only through the existing read-only Flagsmith planner when
the specification contains the required selectors. The feature catalog does
not prove that a flag is configured for experimentation. Warehouse status and
SDK instrumentation remain unknown unless evidence is explicitly provided to
the deterministic input; the language model cannot establish them.

## Processing and validation

1. The provider receives the bounded user request, constraints, and structured
   user fields as data. Prompt instructions prohibit filling unknown external
   facts or treating user text as policy.
2. A strict structured response schema is requested. Malformed, refused,
   incomplete, or type-invalid output fails with a sanitized provider error.
3. Output is parsed into the generated draft envelope, then into the existing
   strict `ExperimentSpecification`. Allocation values must be JSON numbers;
   strings are rejected.
4. High-risk inferred values (project/environment/feature identifiers,
   variant values and allocations, metric event/aggregation/direction, and
   instrumentation fields) are withheld unless exactly supported by user text
   or supplied in a structured overlay. Suggestions remain separately marked
   for confirmation.
5. The resulting specification is passed to `ExperimentPlanningService`,
   which runs the same deterministic prerequisite inspection and validation
   used by `/plan`. AI-generated experiment name and hypothesis require
   explicit confirmation before a plan can be considered ready.

The readiness states and validation checks are defined in
[`13-EXPERIMENT-PLANNING-AND-VALIDATION.md`](13-EXPERIMENT-PLANNING-AND-VALIDATION.md).
Warnings and unknown prerequisites are preserved; drafting never promotes
them to successful checks.

## Provider configuration

The feature is disabled unless these values are configured in the ignored
repository-root `.env`:

```ini
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-3.5-flash-lite
LLM_TIMEOUT_SECONDS=20
```

The initial provider is Google Gemini, accessed through the official
`google-genai` Python SDK. `GEMINI_MODEL` defaults to
`gemini-3.5-flash-lite`. Gemini native JSON response mode receives the generated
JSON Schema through `response_json_schema`; the backend also validates every
field against its Pydantic model. Timeout is bounded to 120 seconds and
cancellation is propagated. Missing or placeholder credentials return
`LLM_NOT_CONFIGURED`; there is no fallback provider. Mock-provider development
and automated tests require no Gemini key and make no live Gemini requests.
To enable live requests, set a real `GEMINI_API_KEY` in the ignored root
`.env`, keep `GEMINI_MODEL` set to the supported model, restart the backend,
and call the draft endpoint. That sends the submitted request data to Google.

## Errors and privacy

Gemini errors use separate safe codes: `GEMINI_AUTHENTICATION_ERROR`,
`GEMINI_MODEL_NOT_FOUND`, `GEMINI_REQUEST_REJECTED`,
`GEMINI_SCHEMA_REJECTED`, `GEMINI_RATE_LIMITED`,
`GEMINI_SERVICE_UNAVAILABLE`, `GEMINI_TIMEOUT`, `GEMINI_EMPTY_RESPONSE`,
`GEMINI_BLOCKED_RESPONSE`, `GEMINI_INVALID_JSON`, and
`GEMINI_OUTPUT_VALIDATION_FAILED`. API errors retain an allowlisted upstream
HTTP status and, when recognized, an allowlisted RPC status/reason. The API
response and structured logs never include provider response bodies, response
headers, credentials, or prompt text. Request validation responses omit
submitted values. The SDK async and sync client resources are closed after
each request, and SDK retries are explicitly limited to one attempt.

The provider transport schema is derived from the generated Pydantic schema:
local `$defs` references are inlined; `const` values are represented as enums
or null types. No domain validators are removed: returned JSON is still parsed
and validated by the original strict Pydantic models, and the deterministic
planner remains authoritative. Gemini's current structured-output
documentation lists types, nullable type arrays, `anyOf`, enums, and closed
objects, and includes `$ref` examples; it does not list `const` in its schema
subset. It also warns that large/deep schemas may be rejected with HTTP 400.
See [Gemini structured output](https://ai.google.dev/gemini-api/docs/structured-output).

### Offline investigation of the first live failure — 2026-11-27

The one prior smoke request was normalized by the application to
`LLM_INVALID_RESPONSE` / HTTP 502. That status is the application's response
status; the diagnostic did not retain an upstream Gemini status or identify
whether the failure occurred during the request, response extraction, JSON
parsing, or Pydantic validation. Therefore it cannot establish whether the
request reached Gemini or whether authentication succeeded.

Offline inspection of the prior generated schema found six `const` keywords,
six `$defs`, seven references, and sixteen `anyOf` nodes. The unsupported
`const` use makes an HTTP 400 schema rejection a plausible cause; schema
complexity is another possibility. This is a hypothesis only. The configured
model remains exactly `gemini-3.5-flash-lite`; no fallback is configured.
Current Google model documentation lists this model, but account access and
availability were not tested. No live request was made during this
investigation.

The adapter now normalizes provider HTTP errors by status and safe reason,
extracts candidate text without relying on the SDK's convenience property,
and distinguishes empty/blocked output, invalid JSON, and Pydantic output
validation. Regression tests exercise these branches with mocked SDK objects.
That investigation was followed by a separate one-request acceptance test,
recorded below. The historical failure's cause remains unknown; the later
success does not reconstruct its raw response or explain that failure.

### Provider DTO diagnostics and normalization — 2026-11-27

The earlier provider DTO marked nullable/domain-optional fields as required in
its generated JSON Schema. The prior smoke test did not preserve Pydantic's
field-level diagnostics, so its exact failing field cannot be reconstructed.
The DTO now accepts omitted optional properties as unknowns, while still
requiring the top-level `specification` object, forbidding extra fields, and
strictly validating supplied enums and types. An explicit mapper converts the
DTO to `ExperimentSpecification`; domain validation and the deterministic
planner remain unchanged.

On a Pydantic failure, internal structured logs retain only the error type,
sanitized field path, expected type/constraint, actual JSON value type, and a
missing-required flag. Values, JSON, prompts, headers, and credentials are
never retained. The public error response exposes only the normalized error
code. Offline tests cover omitted and explicit null values, nested enum/type
errors, numeric allocations, provenance, clarification generation, and the
full mocked adapter-to-planner pipeline.

The application does not send Flagsmith credentials, client objects, or
discovered Flagsmith response data to the LLM. The model has no tools, network
selection, file access, or source-editing capability. The endpoint makes no
Flagsmith writes and does not call an LLM in automated tests; tests use a fake
structured-output provider. Since the request text is sent to the configured
external provider, callers should omit secrets and unnecessary personal data.

## Limitations and deferred work

- One provider is implemented; no provider failover is enabled.
- No real LLM request is part of automated verification.
- The service cannot verify SDK instrumentation or warehouse connectivity.
- The model is a drafting aid, not a deterministic natural-language parser;
  all inferred content must be reviewed.
- Suggestions are returned, but this endpoint does not itself persist a
  multi-turn conversation. The caller resubmits explicit confirmations.
- There is no authentication/authorization layer, approval workflow, database,
  frontend, Flagsmith write, or experiment execution in this milestone.
- Provider-side retention and data handling depend on the Google Gemini API
  account and applicable policy; this application does not claim a
  non-retention guarantee.

### Structural parser diagnostics — 2026-11-27

The third live attempt reported only `provider_structure_invalid` at the
generic provider-structure path. Its raw text was not retained, so the exact
condition and field from that response remain unknowable. The response
contract is a JSON object with required `specification` and optional
`ambiguities`. Provenance, suggestions, and clarification questions are
computed after DTO parsing and are not provider keys.

The parser now distinguishes root type, top-level missing/extra keys,
`specification` object type, `variants` array/null type, variant item object
type, Pydantic field errors, and invalid ambiguity paths/reasons. Allocation
type errors are reported by the DTO's Pydantic validator. Diagnostics retain
only allowlisted paths and key names, type/category metadata, and root/key
shape; unrecognized key names are masked. Normalized errors are raised without
chaining decoder, Pydantic, or SDK exceptions that could retain raw values.
Synthetic parser fixtures exercise each condition; they are not asserted to
reproduce the previous live response.

### Canonical ambiguity paths

`app/domain/experiments/ambiguity_registry.py` is the authoritative registry
for supported ambiguity paths. Its paths map to actual experiment, variant,
metric, or instrumentation model fields. The same registry supplies the
Gemini prompt's allowed path list and the provider DTO's enum. Clarification
generation uses registry guidance. Numeric collection-index aliases such as
`variants[0].allocation` are normalized to the registered
`variants[].allocation` path; unknown paths and duplicate canonical paths are
rejected. Offline tests cover the registry, schema/prompt consistency, aliases,
and unsupported paths.

### Live Gemini acceptance — 2026-11-27

One synthetic drafting request was sent through the configured Google Gemini
provider using `google-genai 3.6.1` and `gemini-3.5-flash-lite`. It returned
HTTP 200 and passed JSON extraction, provider DTO validation, canonical
ambiguity validation, domain mapping, provenance generation, clarification
generation, deterministic planning, and final API response generation. No
retry or model fallback was used.

The primary metric event name and variant allocations remained missing;
warehouse and SDK instrumentation readiness remained UNKNOWN; AI suggestions
required confirmation; and no unsupported Flagsmith reference or Flagsmith
write occurred. The response's ambiguity paths were canonical. Whether alias
normalization was needed cannot be determined because raw provider output was
not retained. Live rejection of an unsupported path was not tested; offline
tests cover that behavior. This verifies the stated one-request pipeline, not
all provider outputs or future availability.
