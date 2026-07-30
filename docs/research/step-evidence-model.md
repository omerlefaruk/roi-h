# Research: Canonical step evidence model

Date: 2026-07-30

Repository snapshot: `404918d`

## Decision

Keep the operator concept of a **step**, but remove `rpa.step` as a durable graph object.
A step is a read-side projection of one `rpa.invocation` and its canonical ActiveGraph
`tool.requested` / `tool.responded` events.

Keep `rpa.invocation`. It is control state, not a second evidence record: it schedules an
attempt, crosses the approval gate, carries idempotency identity, and permits interrupted
writes to become `outcome_unknown`.

ActiveGraph events remain the only execution outcome evidence. `rpa.invocation` keeps the
redacted arguments required before dispatch and approval, but a durable step must not
copy them or any output, failure, or timing.

## Why

### A durable step is a second authority

`StepRecord` repeats invocation identity, arguments, phase, approval, error, and timing.
It also repeats the request arguments and response output, error, and latency already in
tool events. One completed attempt currently writes a response event, a step object, and
an invocation patch. A process stop between those writes can leave three different views
of the same outcome.

Sources: [ROI-H records](../../src/roi_h/harness/records.py),
[ROI-H invocation runtime](../../src/roi_h/harness/invocation_runtime.py).

### Removing the step view would remove useful product behavior

Phase counts, run status, operator trace, observer stories, and automation distillation
all use steps. These callers need one shared projection, not separate event joins and not
a durable copy.

Sources: [phase access](../../src/roi_h/harness/graph_access.py),
[run application](../../src/roi_h/harness/application.py),
[agent reads](../../src/roi_h/agent/read_operations.py),
[observer projection](../../src/roi_h/observer/projection.py),
[workflow distillation](../../src/roi_h/harness/journeys.py).

### The invocation object has a separate job

An invocation triggers execution when its scheduled object is created. Approval defers
that creation through ActiveGraph's durable approval queue. The object also supports
idempotent attempt lookup and crash recovery. Tool events alone do not replace these
control functions without redesigning scheduling and approval.

Source: [ROI-H invocation runtime](../../src/roi_h/harness/invocation_runtime.py).

## Canonical links

ActiveGraph events are immutable and carry `id`, `actor`, `caused_by`, `frame_id`, and
timestamp in the event envelope. Its tool cache pairs a response to a request only when:

```text
tool.responded.caused_by == tool.requested.id
```

ROI-H does not currently meet this rule. Its behavior emitter gives both events the
invocation object's creation event as `caused_by`, then puts `tool_request_event_id` in
the response payload. ActiveGraph does not use that payload field for replay or causal
trace. The event pair must be fixed before it becomes the source for new step
projections.

The canonical joins are:

```text
tool.requested.payload.call_id -> rpa.invocation.data.invocation_id
tool.responded.caused_by       -> tool.requested.id
```

An attempt discriminator must also be present because retries can share an invocation
identity. Store `attempt` in the request payload or make `call_id` attempt-specific.

Sources: [ActiveGraph Event](https://github.com/yoheinakajima/activegraph/blob/v1.10.0/activegraph/core/event.py),
[ActiveGraph tool lifecycle](https://github.com/yoheinakajima/activegraph/blob/v1.10.0/activegraph/runtime/runtime.py),
[ActiveGraph tool cache](https://github.com/yoheinakajima/activegraph/blob/v1.10.0/activegraph/tools/cache.py),
[ActiveGraph behavior graph](https://github.com/yoheinakajima/activegraph/blob/v1.10.0/activegraph/runtime/behavior_graph.py).

## Minimum operator-visible step

The containing operation supplies `run_id`. The step projection contains only:

| Field | Source |
|---|---|
| `step_id` | Actual `tool.requested` event ID |
| `response_event_id` | Actual `tool.responded` event ID, or `null` |
| `sequence` | Durable ActiveGraph store order of the request |
| `invocation_id` | Request `call_id` / invocation identity |
| `attempt` | Invocation and request attempt discriminator |
| `tool` | Canonical `skill.tool` name from the request |
| `phase_id` | Invocation |
| `status` | Derived lifecycle |
| `args` | Redacted `tool.requested.payload.args` |
| `output` | Redacted `tool.responded.payload.output`, or `null` |
| `error` | Structured `tool.responded.payload.error`, or `null` |
| `started_at` | Request event timestamp |
| `completed_at` | Response timestamp, or `null` |
| `duration_seconds` | `tool.responded.payload.latency_seconds`, or `null` |

Do not repeat `name`, separate `skill` and `tool`, scope, effect, approval ID,
idempotency key, actor, or phase name. Those values remain available from the tool
contract, invocation, approval, phase, or event envelope.

Status rules:

- response with no error: `ok`;
- response with an error: `error`;
- request without a response while execution is active: `running`;
- request without a response after recovery: `outcome_unknown`.

A pending approval is not a step because no tool was requested. A pre-dispatch denial is
an invocation or approval failure, not invented tool evidence.

## Invariants

1. ActiveGraph store sequence is order authority. Timestamps are display data.
2. Each projected step has exactly one request and at most one response.
3. A response links to its request through `Event.caused_by`.
4. Request and response tool name and argument hash agree.
5. Invocation identity plus attempt identifies one request.
6. Response evidence determines `ok` or `error` even if a later invocation patch is
   missing. Recovery repairs the invocation from that evidence before it classifies an
   attempt.
7. A request with no response after interrupted external I/O becomes `outcome_unknown`;
   ROI-H
   does not automatically retry a write.
8. Arguments, output, and errors are redacted before event emission.
9. Event IDs are actual ActiveGraph IDs. Sequence cursors remain separate.
10. Old customer history remains readable and is not rewritten.

## Compatibility and implementation gates

This decision does not delete code yet. Replace the model in this order:

1. Split execution across supported behaviors. The invocation behavior patches `running`
   and emits `tool.requested`. A behavior subscribed to `tool.requested` runs the isolated
   invoker and emits `tool.responded`; its normal behavior emitter then sets
   `caused_by` to the request ID. A behavior subscribed to `tool.responded` patches the
   invocation terminal state. Do not use ActiveGraph private graph or runtime methods.
   ActiveGraph 1.10.0 has no verified public standalone recorded-tool operation.
2. Extend the version-locked read adapter to return actual event ID, actor, `caused_by`,
   and frame ID while retaining sequence pagination.
3. Add one bounded, sequence-ordered step projector. A page uses one sequence watermark,
   pages requests by request sequence, and performs bounded response lookups by actual
   request event ID at the same watermark. A new read gets a new snapshot; one cursor
   never mixes snapshots. Use this projector for live status, phase counts, agent trace,
   observer output, automation distillation, and write-operation results.
4. Make recovery event-first. A running invocation with a response is repaired to
   `succeeded` or `failed`. Only a request without a response becomes
   `outcome_unknown`. Completed-attempt lookup uses the same projector, not `step_id`.
5. Replace the write result with a tagged invocation outcome. An attempted tool returns
   its projected step. Pending approval returns `pending_approval` plus `approval_id` and
   no step. Pre-dispatch rejection records a terminal failed invocation, returns its
   structured error, and creates no tool step. Approval and an idempotent repeat use the
   same result builder, so no caller needs a durable step object or a placeholder
   `step_id`.
6. Merge legacy and new history per `(invocation_id, attempt)`. Prefer a canonically
   linked event projection. Otherwise return the matching legacy `rpa.step`, including
   old pre-dispatch steps that have no request or invocation. Never return both. Keep
   legacy `ok` / `error` statuses and the same statuses in the new projection. Do not
   rewrite stored history.
7. After parity checks pass, stop creating `rpa.step`; remove `StepRecord`,
   `InvocationRecord.step_id`, duplicate terminal fields, and all direct step-object
   scans.
8. Keep compaction disabled until the projector can resolve required tool events from
   both live and archived history.

## Focused acceptance checks

- A successful attempt has one invocation, one request, one response, canonical causal
  linkage, and one projected step. No `rpa.step` object is created.
- A failed tool response projects one structured failed step.
- A denied approval emits no tool request and no step.
- A crash after request and before response projects `outcome_unknown` after recovery.
- A crash after response and before the invocation patch repairs and returns the recorded
  `ok` or `error` result; it does not become unknown or execute again.
- Direct invocation, approval, rejection, and idempotent repeat return the tagged outcome
  without a durable step lookup.
- Reopening or retrying a completed attempt does not execute it again.
- Run and phase counts match legacy behavior.
- Workflow distillation preserves event order, tool arguments, output references, and
  phase membership.
- A mixed legacy/new run merges by invocation and attempt, keeps unmatched legacy
  pre-dispatch failures, and never double-counts an attempt.
- A paged projection cannot miss a response or change one request's status inside its
  fixed sequence watermark.
- Secrets do not occur in invocations, events, projections, results, or traces.

## Rejected options

- **Keep durable `rpa.step`:** rejected because it duplicates canonical evidence and can
  diverge after interruption.
- **Remove steps completely:** rejected because the operator and automation journeys
  need a concise operation view.
- **Use invocation as execution evidence:** rejected because mutable control state must
  not replace immutable event evidence.

## Sources

- [ActiveGraph 1.10.0 events](https://github.com/yoheinakajima/activegraph/blob/v1.10.0/docs/concepts/events.md)
- [ActiveGraph 1.10.0 event type](https://github.com/yoheinakajima/activegraph/blob/v1.10.0/activegraph/core/event.py)
- [ActiveGraph 1.10.0 tool lifecycle](https://github.com/yoheinakajima/activegraph/blob/v1.10.0/activegraph/runtime/runtime.py)
- [ActiveGraph 1.10.0 tool cache](https://github.com/yoheinakajima/activegraph/blob/v1.10.0/activegraph/tools/cache.py)
- [ActiveGraph 1.10.0 SQLite event order](https://github.com/yoheinakajima/activegraph/blob/v1.10.0/activegraph/store/sqlite.py)
- [Direct ActiveGraph capability decision](https://github.com/omerlefaruk/roi-h/issues/5)
- [ROI-H issue 12](https://github.com/omerlefaruk/roi-h/issues/12)
