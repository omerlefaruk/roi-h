# Research: Direct ActiveGraph 1.10.0 capability set

## Summary

ROI-H must use ActiveGraph directly for the event log, run registry, graph projection, replay, provenance, behaviors, action-class authority, approvals, budgets, fork/diff/promote, and compaction snapshots. ROI-H must keep only product semantics and adapters that ActiveGraph 1.10.0 does not supply: project and environment selection, skill discovery, isolated subprocess execution, logical paths, artifacts and handoffs, immutable automation packages, product phase and invocation objects, operational store care, and one read-only observer adapter.

The smallest supported boundary is `RunSession` over the public `Runtime` and `Graph` APIs, plus `IsolatedSkillInvoker`, `RunStorage`, and an installed-version-specific read-only projection adapter. ROI-H must not wrap or reproduce the general event store, replay, fork, diff, snapshot, projection, behavior scheduler, budget, or authority logic.

## Verified capability matrix

| Need | ActiveGraph 1.10.0 verified capability | Direct ROI-H choice | Current ROI-H overlap |
|---|---|---|---|
| Durable runs | `Runtime(..., persist_to=...)`, `Runtime.load(...)`, the per-run `EventStore`, and the SQLite `runs` registry. Event order is SQLite `seq`, not time. | **Use directly.** One SQLite file per ROI-H project environment; one ActiveGraph run per ROI-H run. | `rpa.run` repeats goal and product status. Keep it only as a product object; the native run row and event stream stay authoritative. [ActiveGraph `activegraph/store/base.py`](https://github.com/yoheinakajima/activegraph/blob/v1.10.0/activegraph/store/base.py) [ActiveGraph `activegraph/store/sqlite.py`](https://github.com/yoheinakajima/activegraph/blob/v1.10.0/activegraph/store/sqlite.py) [ROI-H `src/roi_h/harness/application.py`](../../src/roi_h/harness/application.py) [ROI-H `src/roi_h/harness/records.py`](../../src/roi_h/harness/records.py) |
| Staged development | Durable `fork()`, structural `diff()`, dry-run `promote()`, and fail-closed promote supply the supported branch/test/adopt loop. Packs can load candidate behaviors. | **Use directly for experimental branches.** Keep ROI-H phases for business workflow checkpoints; they are not graph branches. Keep immutable `ship` as the product publication gate; a promote does not move code or packs. | Phase retry and `seed_from_handoff()` copy state and files but do not give native lineage or structural diff. Do not extend them into a second branching system. [ActiveGraph fork/test/promote guide](https://github.com/yoheinakajima/activegraph/blob/v1.10.0/docs/guides/fork-test-promote.md) [ROI-H `src/roi_h/harness/phase_machine.py`](../../src/roi_h/harness/phase_machine.py) |
| Authority | Closed `R0`-`R4` action classes, event-backed ceilings, per-capability lower ceilings, `authority.decision`, `propose_object()`, `pending_approvals()`, and `approve()`. R3 always needs approval; R4 always uses the governance gate. | **Use directly.** ROI-H maps declared tool effects to action classes and can add stricter product policy, but must not make a second authority engine. | `_ACTION_CLASS`, `force`, `auto_approve`, and `requires_approval` are product routing. `ROIHRuntime.reject_approval()` is a compatibility extension because 1.10.0 exposes grant but no public reject operation. [ActiveGraph `activegraph/runtime/authority.py`](https://github.com/yoheinakajima/activegraph/blob/v1.10.0/activegraph/runtime/authority.py) [ROI-H `src/roi_h/harness/invoke_ops.py`](../../src/roi_h/harness/invoke_ops.py) [ROI-H `src/roi_h/harness/activegraph_runtime.py`](../../src/roi_h/harness/activegraph_runtime.py) |
| Evidence and audit | Append-only events, actors, `caused_by`, object provenance, behavior lifecycle, tool/LLM request-response events, causal-chain trace, and optional `context.read`. | **Use directly.** Enable `trace_context_reads=True` only when its extra events are wanted. Store product artifact digests as typed graph objects. | ROI-H `rpa.step` copies args, output, errors, and duration already present in tool events. Its custom standalone tool path also manually emits `tool.requested` and `tool.responded`. Reduce `rpa.step` to a product projection/reference when compatibility permits. [ActiveGraph events](https://github.com/yoheinakajima/activegraph/blob/v1.10.0/docs/concepts/events.md) [ActiveGraph `activegraph/trace/causal.py`](https://github.com/yoheinakajima/activegraph/blob/v1.10.0/activegraph/trace/causal.py) [ROI-H `src/roi_h/harness/invocation_runtime.py`](../../src/roi_h/harness/invocation_runtime.py) |
| Replay | `Runtime.load()` rebuilds the graph. Strict replay checks divergence. LLM and tool responses are content-hash cached only on the runtime-owned call path. | **Use directly.** Do not implement replay from ROI-H records. | The observer replays only `object.created` and `patch.applied` with local SQL. This duplicates and narrows ActiveGraph projection semantics. Replace it when ActiveGraph supplies a safe read-only load API. [ActiveGraph replay](https://github.com/yoheinakajima/activegraph/blob/v1.10.0/docs/concepts/replay.md) [ROI-H `src/roi_h/observer/activegraph_adapter.py`](../../src/roi_h/observer/activegraph_adapter.py) |
| Lineage | Run rows store `parent_run_id` and `forked_at_event_id`; object and relation provenance stores causal event links; promote records its source fork. | **Use directly.** Add ROI-H domain relations only for business meaning, not run ancestry. | `seeded_from`, `source_run_id`, and `source_phase_id` describe copied handoffs but are not native fork lineage. Keep those labels, but do not present them as ActiveGraph lineage. [ActiveGraph `activegraph/store/base.py`](https://github.com/yoheinakajima/activegraph/blob/v1.10.0/activegraph/store/base.py) [ROI-H `src/roi_h/harness/records.py`](../../src/roi_h/harness/records.py) |
| Forks and comparison | SQLite `fork_run()` copies an inclusive event prefix; `Runtime.fork()` replays it; `Runtime.diff()` compares final objects, relations, and event tails. | **Use directly.** Expose only the minimum product journey that needs it; keep the full runtime public for advanced use. | No complete duplicate exists. Handoff seeding is a file/product transfer, not a fork. [ActiveGraph forking](https://github.com/yoheinakajima/activegraph/blob/v1.10.0/docs/concepts/forking.md) [ActiveGraph `activegraph/runtime/diff.py`](https://github.com/yoheinakajima/activegraph/blob/v1.10.0/activegraph/runtime/diff.py) |
| Behaviors | Event, predicate, pattern, and view subscriptions; deterministic dispatch; lifecycle and failure events; budgets. | **Use directly.** ROI-H behaviors should contain only product reactions and call product adapters. | ROI-H correctly uses behaviors for approval materialization and invocation execution. The execution behavior duplicates ActiveGraph's private `_invoke_tool` lifecycle because no public standalone recorded-tool call is verified. Keep the adapter, not a second scheduler. [ActiveGraph behaviors](https://github.com/yoheinakajima/activegraph/blob/v1.10.0/docs/concepts/behaviors.md) [ROI-H `src/roi_h/harness/invocation_runtime.py`](../../src/roi_h/harness/invocation_runtime.py) |
| Snapshots | `compact()` creates a `runtime.snapshot`, stores a hash-keyed canonical graph blob, and archives the earlier event prefix. `verify_snapshot()` replays the archive and checks the hash. | **Use directly only for offline retention.** Do not use it as a user checkpoint or automation-package snapshot. | Phase handoffs and automation packages use the word or idea of a snapshot, but they contain product files and code. They do not duplicate ActiveGraph compaction. [ActiveGraph `activegraph/store/retention.py`](https://github.com/yoheinakajima/activegraph/blob/v1.10.0/activegraph/store/retention.py) [ROI-H `src/roi_h/harness/phases.py`](../../src/roi_h/harness/phases.py) |
| Projections | The in-memory graph is the rebuildable projection of the event log; `Graph.objects()`, `relations()`, and `get_object()` are supported reads. A `View` is a per-behavior read scope, not a stored snapshot. | **Use directly inside live and reopened sessions.** Keep observer formatting as a product projection over native objects. | `ActiveGraphProjectionAdapter.project_run()` manually applies only object creation and patches. This is a temporary schema adapter, not a second authority. [ActiveGraph graph](https://github.com/yoheinakajima/activegraph/blob/v1.10.0/docs/concepts/graph.md) [ActiveGraph views](https://github.com/yoheinakajima/activegraph/blob/v1.10.0/docs/concepts/views.md) [ROI-H `src/roi_h/observer/activegraph_adapter.py`](../../src/roi_h/observer/activegraph_adapter.py) |

## Current duplication and disposition

1. **High — manual observer replay:** `src/roi_h/observer/activegraph_adapter.py` knows the SQLite tables and reimplements a partial projector. Keep it isolated and version-locked only because the public `EventStore` has no read-only run-list/projection operation and `SQLiteEventStore` opens read-write and ensures schema. Delete it when upstream supplies a non-mutating read API.
2. **High — manual standalone tool lifecycle:** `src/roi_h/harness/invocation_runtime.py` repeats input/output validation, budget use, cache hashes, and `tool.requested`/`tool.responded` emission found in private `Runtime._invoke_tool`. Do not call that private method. Retain the current behavior plus `IsolatedSkillInvoker` until ActiveGraph has a public standalone recorded-tool API; then delete the duplicate lifecycle.
3. **Medium — evidence copied into steps:** `rpa.step` repeats canonical tool arguments, output, error, latency, and identity. Keep only fields needed for the operator story and stable product compatibility. New evidence links should point to canonical event IDs.
4. **Medium — run metadata overlap:** `rpa.run.goal` overlaps the native `runs.goal`; `rpa.run.status` is product state that ActiveGraph does not supply as one field. Make the native row and events authoritative. Keep the object as a product projection for phase plan, package identity, cancellation, and operator status.
5. **Low — approval bridge:** `rpa.approval.requested` plus `request_approval` only converts a product invocation into ActiveGraph `approval.proposed`. This is a small adapter. `ROIHRuntime.reject_approval` must remain until upstream adds supported rejection and replay restoration.
6. **Not duplication:** phase handoff files, artifact bytes/digests, isolated process execution, logical paths, secret injection, package shipping, backup/restore, and diagnostics around a failed store are product or operational boundaries absent from ActiveGraph.

## Smallest retained integration boundary

Retain these seams only:

- `RunSession`: select a workspace, construct or load `Runtime`, register ROI-H tools and behaviors, and expose the native runtime.
- `IsolatedSkillInvoker`: implement ActiveGraph's tool-invoker shape with ROI-H subprocess, path, secret, and production-policy controls.
- Two small product behaviors: turn an ROI-H invocation into a native approval proposal; turn a scheduled invocation into an isolated tool attempt and product step until a public standalone tool API exists.
- `RunStorage` and phase/package services: own bytes, digests, logical URIs, handoffs, and immutable automation packages; record their identities in the graph.
- `StoreLifecycle`: operational SQLite inspect, consistent backup, restore, and locks only. It must not wrap event append, replay, fork, diff, snapshots, or projection semantics.
- `ActiveGraphProjectionAdapter`: one read-only, ActiveGraph-1.10-specific SQL adapter for the observer, with contract tests. No SQL can escape this file.
- `ROIHRuntime`: only the missing approval-rejection compatibility method. Remove the subclass when upstream supports it.

Everything else calls public ActiveGraph APIs directly: `Runtime`, `Graph`, `Behavior`, authority evaluation, approval grant/list, budget/status, load/replay, fork/diff/promote, trace, and retention functions.

## Deletions and deferrals

- Delete no product code in this research ticket.
- Defer a fork CLI until one named ROI-H workflow needs branch/test/promote. The public `RunSession.runtime` already gives the advanced API.
- Defer compaction. ActiveGraph says it is offline per run, limits fork points below the horizon, and leaves causal-chain/archive limits.
- Defer a second projection database and all general event-store wrappers.
- Defer replacement of manual tool events until ActiveGraph exposes a public standalone invocation method with caller idempotency keys and a custom invoker.
- Defer removal of the observer SQL adapter until an upstream read-only API can list runs and project a run without creating schema or taking write access.
- Do not map ROI-H `read/write/destructive` labels with more policy logic than the current explicit table. Long term, tool metadata should declare canonical `action_class` directly.

## Compatibility risks

- **High:** ActiveGraph forks and promote require SQLite runtimes in the same store; Postgres must migrate first. Compaction can remove old fork points from the hot log.
- **High:** ROI-H standalone tool calls do not use the public ActiveGraph replay-cache path. ActiveGraph's supported native tool path is internal to `LLMBehavior`; private `_invoke_tool` is not an integration boundary.
- **High:** The observer depends on ActiveGraph SQLite schema version `1` and currently ignores removals, relations, snapshots, and archive history. Compacted runs can therefore project incorrectly.
- **Medium:** ActiveGraph SQLite forces WAL and `synchronous=NORMAL`. ROI-H cannot claim `FULL` production durability without an upstream configuration seam.
- **Medium:** `ROIHRuntime` reads `_pack_state`, a private ActiveGraph attribute. A 1.10 patch change can break rejection restore. Pinning `activegraph==1.10.0` limits but does not remove this risk.
- **Medium:** ActiveGraph views are not snapshots. A product checkpoint must stay a handoff/package or a durable fork; it must not be described as a view.
- **Low:** `trace_context_reads` is off by default and changes event logs when enabled. Treat the setting as an explicit compatibility choice.

## Unresolved facts

- ActiveGraph 1.10.0 has no verified public approval-rejection API.
- It has no verified public API for one standalone, non-LLM tool invocation that owns the tool event pair, replay cache, custom idempotency key, and custom invoker.
- It has no verified read-only `Runtime.load` or run-catalog API that guarantees no schema creation or write-mode SQLite open.
- ROI-H needs a product decision on whether `rpa.step` stays a stable public object or becomes a smaller projection that references canonical tool event IDs.
- The exact operator workflow that needs fork/diff/promote is not yet named; do not add a mirrored CLI before it is.

## Issue-ready resolution summary

Choose ActiveGraph 1.10.0 as the direct owner of durable run/event storage, native graph projection, behavior scheduling, budgets, authority and approval grant, evidence/provenance, load/replay, lineage, fork/diff/promote, and retention snapshots. Retain ROI-H only for product-domain objects, phases and handoffs, artifact/package bytes, isolated skill execution, paths/secrets/policy, store operations, approval rejection compatibility, and one version-locked read-only observer adapter. Treat manual standalone tool events and observer replay as temporary compatibility seams, not architecture; delete each when an upstream public API covers it. Do not build a second event store, replay engine, fork model, snapshot model, projection database, or authority engine.

**One-line map gist:** `ROI-H product shell → RunSession → public ActiveGraph Runtime/Graph; side seams only: isolated ToolInvoker, RunStorage, StoreLifecycle, read-only 1.10 projection adapter, temporary approval reject.`

## Sources

- Kept: installed/official ActiveGraph v1.10.0 source and docs at `https://github.com/yoheinakajima/activegraph/tree/v1.10.0` — exact pinned implementation and contracts.
- Kept: ROI-H source under `src/roi_h/harness/`, `src/roi_h/observer/`, `README.md`, and `docs/project-storage-activegraph-refactor.md` — current behavior and callers.
- Dropped: search summaries and third-party framework pages — not primary sources.
- Dropped: ActiveGraph main-branch material where it could differ from tag `v1.10.0` — the installed pin is authoritative.
