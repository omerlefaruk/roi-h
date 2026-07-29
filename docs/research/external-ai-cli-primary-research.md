# External AI CLI design: primary-source research

Date: 2026-07-29

Repository snapshot: `e8a3994` (`refactor project storage around ActiveGraph`)

## Question

How should ROI-H expose its full product through a CLI so that an external AI can use it safely and reliably?

## Conclusion

ROI-H needs one typed operation registry. The registry must be the stable product interface. It must not expose SQLite tables or Python internals.

Three adapters must use the same registry:

1. The current human CLI.
2. A strict JSON and JSON Lines agent interface.
3. An optional MCP server.

This design keeps one implementation of permissions, approvals, idempotency, schemas, and errors. It also prevents the CLI and MCP interface from changing in different ways.

The current commit has many required domain functions. It has project export and import, store maintenance, logical files, artifacts, retention plans, diagnostics, approvals, automation shipping, and a dry-run option. The main missing part is a stable machine contract around these functions.

## Primary-source findings

### 1. A CLI needs a strict process contract

POSIX says that an invalid option or a missing option argument must produce a diagnostic on standard error and a nonzero exit status. POSIX also defines `--` as the boundary that protects operands from option parsing. Shells use `126` when a command exists but cannot run and `127` when a command does not exist. A signal normally produces a status greater than `128`. ROI-H must not use these values for its own domain errors. [POSIX utility behavior](https://pubs.opengroup.org/onlinepubs/9699919799/utilities/V3_chap01.html), [POSIX shell exit status](https://pubs.opengroup.org/onlinepubs/9699919799/utilities/V3_chap02.html)

GNU command-line guidance adds the common `--help` and `--version` interface. It also recommends long options that are clear and consistent. [GNU command-line interface standards](https://www.gnu.org/prep/standards/html_node/Command_002dLine-Interfaces.html)

GitHub CLI uses a small set of documented exit codes. This is better for integrations than a large list of numeric codes. [GitHub CLI exit codes](https://cli.github.com/manual/gh_help_exit-codes)

**ROI-H decision**

- Use `0` for a completed command.
- Use `1` for an operation or domain failure.
- Use `2` for an invalid command or invalid input.
- Use `4` when authentication or permission is required.
- Use `130` when the operator cancels the process.
- Keep detailed classification in a structured error code, not in the process exit code.
- Support `--` before positional values.

A read command must return `0` when the read succeeds, even if the resource is in a failed state. For example, `runs.show` must return `data.state: "failed"` and exit `0`. It must not report that the CLI command failed. The current `rpa status` command mixes these two meanings because it changes `ok` when a run has failed steps or pending approvals.

### 2. Machine output must be a versioned interface

Terraform says that terminal text is not a stable interface for integrations. Its JSON mode emits one typed JSON object per line for long operations. The first item identifies the interface version. A minor version can add compatible fields, and consumers must ignore unknown fields. A major version marks an incompatible change. [Terraform machine-readable output](https://developer.hashicorp.com/terraform/internals/machine-readable-ui)

JSON Lines requires UTF-8, one valid JSON value on each line, and a newline delimiter. It is suitable for process communication and logs. [JSON Lines format](https://jsonlines.org/)

GitHub CLI lets callers select JSON fields. If the caller omits the field list, the command shows the available fields. This gives both discovery and bounded output. [GitHub CLI JSON formatting](https://cli.github.com/manual/gh_help_formatting)

**ROI-H decision**

Add global modes:

```text
--output text
--output json
--output jsonl
--no-input
--no-color
--no-pager
```

`json` must write one compact JSON document to standard output. `jsonl` must write one compact event per line. Logs, warnings, progress text, and debug data must go to standard error. JSON modes must never use color, a spinner, or a pager.

The response envelope must be stable:

```json
{
  "contract_version": "1.0",
  "request_id": "req_opaque",
  "command": "runs.show",
  "ok": true,
  "data": {},
  "error": null
}
```

An error must use a stable code:

```json
{
  "contract_version": "1.0",
  "request_id": "req_opaque",
  "command": "tools.invoke",
  "ok": false,
  "data": null,
  "error": {
    "code": "tool.network_error",
    "category": "transient",
    "message": "The tool could not reach the remote service.",
    "retryable": true,
    "retry_after_ms": 1000,
    "details": {},
    "diagnostic_id": "diag_opaque"
  }
}
```

An agent must never need to parse `message`. It must make decisions from `code`, `category`, `retryable`, and typed details. Stripe follows the same general pattern with machine error codes, the related parameter, a request ID, and a human message. [Stripe error handling](https://docs.stripe.com/error-handling), [Stripe request IDs](https://docs.stripe.com/api/request_ids)

### 3. JSON Schema must define all inputs and outputs

JSON Schema 2020-12 is the current released JSON Schema version. It defines validation rules and descriptive metadata for JSON data. [JSON Schema 2020-12](https://json-schema.org/draft/2020-12), [JSON Schema validation specification](https://json-schema.org/draft/2020-12/json-schema-validation)

MCP tool definitions use an input schema and can use an output schema. MCP clients can validate structured tool results against the output schema. [MCP tool specification](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)

**ROI-H decision**

Every operation must declare:

```text
name
contract version
description
input JSON Schema
output JSON Schema
effect: read | write | destructive
idempotency: inherent | supported | required | none
approval rule
secret input paths
open-world flag
sync or task execution
pagination support
implementation handler
```

The schema must declare:

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema"
}
```

ROI-H should reject unknown input fields by default. This catches agent mistakes early. Output consumers must ignore new fields in the same contract major version.

The existing `rpa tools` result is a good start. It already reports tool schemas, effects, approval needs, and idempotency. The registry must extend the same model to all product operations, such as projects, runs, artifacts, automations, skills, approvals, and store maintenance.

### 4. Complex input must not depend on shell quoting

AWS CLI accepts JSON or YAML input files and can generate an input skeleton. GitHub CLI accepts an input file or `-` for standard input. These forms avoid shell quoting errors. [AWS CLI input skeletons](https://docs.aws.amazon.com/cli/latest/userguide/cli-usage-skeleton.html), [GitHub CLI API input](https://cli.github.com/manual/gh_api)

**ROI-H decision**

All operations with structured input must support:

```text
--input @request.json
--input -
```

The current `--args '{"key":"value"}'` form can stay as a convenience. It must not be the only form.

Add these discovery commands:

```text
roi-h agent capabilities --output json
roi-h agent operations --output json
roi-h agent schema OPERATION --output json
roi-h agent call OPERATION --input @request.json --output json
```

The existing resource commands must call the same operations. For example, `roi-h rpa project list` and `roi-h agent call projects.list` must use one handler and one output model.

### 5. Safe retries need caller-controlled idempotency

Stripe accepts an idempotency key for write requests. A retry with the same key returns the first result. Reuse with different parameters is an error. This protects clients from duplicate side effects after a lost response. [Stripe idempotent requests](https://docs.stripe.com/api/idempotent_requests)

**ROI-H decision**

- Read operations are safe to retry.
- Idempotent writes must accept `--idempotency-key`.
- The key must bind to the operation name, project, environment, canonical input, and result.
- A retry with the same key and input must return the stored result.
- The same key with different input must return `idempotency.input_mismatch`.
- Non-idempotent writes must return `retryable: false` after an uncertain result.
- The agent must only retry when ROI-H returns `retryable: true`.

ROI-H already creates invocation idempotency identities and sends them to tools. The external caller cannot supply the key. The registry must connect a caller key to the existing ActiveGraph identity instead of creating a second retry system.

### 6. Destructive work needs plan and apply

Terraform separates planning from applying. A saved plan can later be applied as the reviewed action. Its detailed plan exit mode uses `0` for no changes, `1` for error, and `2` for changes. Kubectl supplies client and server dry-run modes for preview work. [Terraform plan command](https://developer.hashicorp.com/terraform/cli/commands/plan), [Terraform automation workflow](https://developer.hashicorp.com/terraform/tutorials/automation/automate-terraform), [kubectl usage conventions](https://kubernetes.io/docs/reference/kubectl/conventions/)

**ROI-H decision**

Use one plan model for all destructive operations:

```text
roi-h agent plan OPERATION --input @request.json
roi-h agent apply PLAN_ID --expect-digest DIGEST
```

A plan must include:

- The exact effects.
- The affected project, environment, runs, files, and records.
- Required approvals.
- Preconditions.
- A state digest.
- An expiry time.
- A plan ID.

Apply must fail with `plan.state_changed` if the digest no longer matches. It must fail with `plan.expired` after expiry.

The current `gc plan/show/apply` flow is the correct pattern. `store compact` also previews by default. Extend this pattern to project deletion, restore, migration, automation promotion, skill deletion, and other destructive work. Do not use a generic `--force` as the only safety boundary for large changes.

### 7. Lists must be bounded and stable

AWS CLI supports page size, maximum item count, and a starting token. It also separates server-side filtering from client-side queries. MCP uses opaque cursors and a `nextCursor` value. MCP task listings require cursor pagination and require clients to treat cursors as opaque. [AWS CLI pagination](https://docs.aws.amazon.com/cli/latest/userguide/cli-usage-pagination.html), [AWS CLI filtering](https://docs.aws.amazon.com/cli/latest/userguide/cli-usage-filter.html), [MCP task lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/tasks)

**ROI-H decision**

Every unbounded list must support:

```text
--limit N
--cursor TOKEN
--filter EXPRESSION
--sort FIELD
```

The result must contain:

```json
{
  "items": [],
  "next_cursor": null,
  "has_more": false,
  "snapshot": "opaque_watermark"
}
```

Ordering must be deterministic. The cursor must be opaque. The snapshot must prevent missing or duplicate items when data changes during paging. Agent mode must not fetch all pages by default because this can fill the model context.

Large files and artifacts must return metadata and a logical URI by default. The metadata must include size, digest, media type, and export command. ROI-H must not put large binary data in JSON.

### 8. Long operations need tasks, events, wait, and cancel

MCP defines tasks with working and terminal states, progress, polling, cursor-based listing, and cancellation. It also says that clients must not depend on optional status notifications. [MCP task lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/utilities/tasks)

Terraform JSON output shows how to stream typed progress events for long work. [Terraform machine-readable output](https://developer.hashicorp.com/terraform/internals/machine-readable-ui)

**ROI-H decision**

Use these standard run states:

```text
queued
working
input_required
approval_required
succeeded
failed
cancelled
```

Add:

```text
roi-h rpa runs list
roi-h rpa runs show RUN_ID
roi-h rpa runs wait RUN_ID --timeout 60
roi-h rpa runs cancel RUN_ID
roi-h rpa events list --run-id RUN_ID
roi-h rpa events follow --run-id RUN_ID --output jsonl
roi-h rpa trace show --run-id RUN_ID
```

Each streamed event needs an event ID, sequence, timestamp, type, run ID, request ID, invocation ID when present, and typed data. Reconnection must support `--after EVENT_ID`.

### 9. Secret values must not be command arguments

GitHub CLI reads a secret from standard input when no body argument is present. Python `getpass` reads a password without terminal echo. Apple provides Keychain for keys. Windows says that applications must not keep credentials in plain-text app settings and provides Credential Locker for small credentials. [GitHub CLI secret input](https://cli.github.com/manual/gh_secret_set), [Python `getpass`](https://docs.python.org/3.12/library/getpass.html), [Apple Keychain](https://developer.apple.com/documentation/security/storing-keys-in-the-keychain), [Windows Credential Locker](https://learn.microsoft.com/en-us/windows/apps/develop/security/credential-locker)

**ROI-H decision**

Replace:

```text
roi-h rpa secret set NAME VALUE
```

with:

```text
roi-h rpa secret set NAME
roi-h rpa secret set NAME --value-stdin
roi-h rpa secret status NAME
roi-h rpa secret delete NAME
```

The interactive form must hide input. The agent form must read standard input. Secret values must never enter argv, shell history, JSON output, events, traces, plans, diagnostics, or SQLite. Use the OS credential store for the value. Keep only a secret reference and metadata in `~/.roi-h`.

Schemas must mark secret input paths. Redaction must use these paths. Redaction must not depend only on field-name guesses.

### 10. Discovery must serve agents and humans

GitHub CLI can generate completion scripts for Bash, Zsh, Fish, and PowerShell. AWS can generate input skeletons. Kubectl tells scripts to request a machine format, fully qualify versions, and avoid hidden context. [GitHub CLI completion](https://cli.github.com/manual/gh_completion), [AWS CLI input skeletons](https://docs.aws.amazon.com/cli/latest/userguide/cli-usage-skeleton.html), [kubectl usage conventions](https://kubernetes.io/docs/reference/kubectl/conventions/)

**ROI-H decision**

Add:

```text
roi-h version --output json
roi-h capabilities --output json
roi-h doctor --output json
roi-h completion bash|zsh|fish|powershell
```

All agent calls must name the project and environment in the input or receive them in the resolved response. An external AI must not depend on a hidden active project. Sticky context can remain for humans.

Version the contract independently from the package. Breaking field removals, field type changes, new required inputs, and semantic changes require a new contract major version. Additive optional fields can use a minor version. GitHub and Stripe both use explicit API versions to control breaking behavior. [GitHub REST API versions](https://docs.github.com/en/rest/about-the-rest-api/api-versions), [Stripe API versioning](https://docs.stripe.com/api/versioning)

### 11. MCP must be an adapter, not a second product API

MCP tool definitions have JSON Schema inputs and outputs. Its annotations can mark a tool as read-only, destructive, idempotent, or open-world. The annotations are hints, so ROI-H must still enforce its own policy. [MCP tool schemas](https://modelcontextprotocol.io/specification/2025-11-25/server/tools), [MCP tool annotations](https://modelcontextprotocol.io/specification/2025-11-25/schema)

MCP stdio reserves standard input and standard output for JSON-RPC messages. A server can write logs to standard error, but it must not write other data to standard output. Streamable HTTP also needs origin checks, local-only binding for a local service, and authentication. [MCP transports](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports)

**ROI-H decision**

Add this only after the registry and JSON contract are stable:

```text
roi-h mcp serve --stdio
```

Generate MCP tools from the operation registry. Map effect and idempotency fields to MCP annotations. Use the same schemas, handlers, approvals, plans, and error codes. Codex can use MCP when available and use the JSON CLI on other systems.

Do not add Streamable HTTP in the first release. Stdio has a smaller security surface and matches the local Codex use case.

## Current ROI-H assessment

The assessment below comes from the current [CLI](../../src/roi_h/cli.py) and harness modules at commit `e8a3994`.

### Strong existing parts

- The package has one installable `roi-h` entry point.
- Most commands already emit JSON.
- `rpa tools` exposes tool schemas, effects, approvals, scope, and idempotency.
- ActiveGraph owns durable runs, invocations, approvals, and idempotency identities.
- Projects have typed paths, doctor checks, export, and import.
- Store status, check, backup, restore, migrate, and compact exist.
- Run inputs use logical paths.
- Artifacts have IDs, hashes, sizes, and export.
- Garbage collection already uses plan, show, and apply.
- Runs support phases, handoffs, cancellation, reconciliation, budgets, and automation shipping.
- Deterministic automation execution has a dry-run.
- Diagnostics use bounded, redacted JSON Lines with diagnostic IDs.

### Blocking gaps for a full external agent

- There is no contract version or response schema.
- Response envelopes differ by command.
- Exceptions become one English string with exit `1`.
- `status` reports command failure when the run only needs attention.
- There is no global no-input mode.
- There is no stable command and schema discovery interface.
- Structured input depends mainly on shell-quoted JSON.
- There is no run list, event list/follow, wait, or trace command.
- Lists have no shared cursor, limit, filter, and snapshot contract.
- External callers cannot supply an idempotency key.
- Destructive commands do not use one plan/apply contract.
- Secret values are positional argv values.
- There is no complete skill lifecycle interface.
- Automation listing has no show, verify, or compare operation.
- There is no machine-readable version, capabilities, or completion command.
- There is no MCP adapter.

## Target command surface

Keep the current commands as compatible human aliases. Add these canonical operations to the registry:

```text
system.version
system.capabilities
system.doctor

projects.list
projects.show
projects.create
projects.rename
projects.export
projects.import
projects.delete.plan
projects.delete.apply

tools.list
tools.show
tools.invoke

runs.list
runs.show
runs.wait
runs.cancel
runs.reconcile
runs.inputs.add
runs.files.list
runs.events.list
runs.events.follow
runs.trace.show

approvals.list
approvals.approve
approvals.reject

artifacts.list
artifacts.show
artifacts.export

phases.list
phases.begin
phases.end
phases.fail
phases.skip
phases.retry

automations.list
automations.show
automations.verify
automations.compare
automations.run
automations.ship

skills.list
skills.show
skills.validate
skills.promote
skills.delete.plan
skills.delete.apply

secrets.list
secrets.status
secrets.set
secrets.delete

store.status
store.check
store.backup
store.restore.plan
store.restore.apply
store.migrate.plan
store.migrate.apply
store.compact.plan
store.compact.apply

retention.plan
retention.show
retention.apply
```

Do not add raw SQL operations. ActiveGraph and SQLite are implementation details. Direct SQL would bypass validation, policy, approvals, event history, idempotency, and reconciliation.

## Dependency-ordered implementation plan

### Phase 1: Contract foundation

1. Define the operation registry.
2. Define the response, error, page, task, event, and plan schemas.
3. Add contract version `1.0`.
4. Add global output and no-input options.
5. Separate standard output from standard error.
6. Map exceptions to stable error codes.
7. Keep the current command names as adapters.
8. Add golden JSON contract tests.

**Exit condition:** Every existing command can run through one registry handler and emits a valid response envelope in JSON mode.

### Phase 2: Discovery and complete read access

1. Add version, capabilities, operations, schema, and generic call commands.
2. Add `runs.list`, `runs.show`, events, trace, and wait.
3. Add skill show and validate.
4. Add automation show, verify, and compare.
5. Add artifact show.
6. Add shared pagination, filtering, sorting, and field selection.
7. Add `--input @file` and `--input -`.

**Exit condition:** An agent can discover all operations and schemas without reading source code. It can inspect every important product resource without SQL.

### Phase 3: Safe mutation

1. Accept caller idempotency keys.
2. Bind them to the current ActiveGraph invocation identity.
3. Add one plan model and one apply verifier.
4. Move all destructive operations to plan/apply.
5. Add approval rejection and clear approval states.
6. Make every operation declare its effect and retry rule.

**Exit condition:** A network failure cannot silently duplicate a supported write. A destructive action cannot run from an old or changed plan.

### Phase 4: Secret and file safety

1. Remove positional secret values.
2. Add hidden interactive input and standard-input agent input.
3. Store secret values in the OS credential store.
4. Mark secret schema paths.
5. Apply path-based redaction to output, events, plans, traces, and diagnostics.
6. Return logical file and artifact references instead of large content.

**Exit condition:** Secret values do not appear in argv, project files, SQLite, logs, or machine output.

### Phase 5: Long-run agent control

1. Add the standard task states.
2. Add wait, bounded polling, event follow, resume-after-event, and cancel.
3. Add request IDs to all events and diagnostics.
4. Add stable terminal results for each long task.

**Exit condition:** An agent can disconnect, reconnect, find the run, continue after the last event, and retrieve the final result.

### Phase 6: Optional MCP adapter

1. Generate MCP tools from the registry.
2. Use stdio only in the first release.
3. Keep logs on standard error.
4. Add MCP contract tests against the same fixtures as the CLI.

**Exit condition:** The CLI and MCP form return equivalent data and enforce the same policy for each registered operation.

### Phase 7: Agent-only acceptance test

Create an end-to-end test that starts with an installed wheel and an empty home. The test agent must only use published CLI JSON. It must not import ROI-H Python modules or read SQLite.

The test must:

1. Discover capabilities and schemas.
2. Create and select a project.
3. Inspect tools.
4. Start a run.
5. Add an input file.
6. Invoke read and write tools.
7. handle an approval.
8. Follow events and inspect the trace.
9. Export and verify an artifact.
10. Ship an automation.
11. Dry-run and run the frozen automation.
12. Inspect and compare automation versions.
13. Back up and check the store.
14. Prove that no secret appears in output or diagnostics.

**Final exit condition:** Codex or another external AI can use the complete ROI-H product from a clean computer without source-code knowledge, raw SQL, or an embedded AI provider.

## Scope recommendation

Do not replace `argparse` only to make the interface look newer. The framework is not the main problem. PyPA shows several valid Python CLI frameworks and package entry-point patterns. The stable registry and contract matter more than the parser library. [PyPA command-line packaging guide](https://packaging.python.org/en/latest/guides/creating-command-line-tools/)

Build the JSON CLI first. Add MCP after the same interface passes the agent-only acceptance test. This gives Codex an immediate local interface and keeps MCP as a small adapter.
