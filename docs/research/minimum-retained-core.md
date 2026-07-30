# Minimum retained ROI-H core

## Decision

Keep ROI-H as a thin product shell around public ActiveGraph APIs. Keep only the code that enforces the typed AI contract, customer-data ownership, isolated effects, immutable evidence, Python automation publication, and native customer delivery.

Do not delete data-preservation or security seams to reach a line target. Do not keep a second AI orchestrator, recipe engine, human command tree, web observer, delivery system, or copied contract when the accepted replacement exists.

## Verified baseline

This is the pre-report baseline. The audit artifact adds one document and 108 lines on its throwaway branch.

| Group | Tracked files | Lines |
|---|---:|---:|
| `src/roi_h/` | 75 | 20,054 |
| `skills/` | 34 | 1,742 |
| `scripts/` | 6 | 878 |
| `packages/roi-h-installer/` | 11 | 2,844 |
| `docs/` | 10 | 7,528 |
| `tests/` | 49 | 7,417 |

The current check passed: `PYTHONPATH=.:src uv run pytest -q` returned 147 passed and one Windows-only skip.

## Minimum retained product seams

Keep these seams. They protect the destination or customer data.

- The model-neutral typed operation contract, dispatcher, idempotency claims, approvals, and plan/apply gates.
- `RunSession` over the public ActiveGraph `Runtime` and `Graph` APIs.
- The temporary standalone invocation adapter because ActiveGraph 1.10.0 has no public equivalent.
- The version-locked, read-only ActiveGraph projection adapter because ActiveGraph has no non-mutating run-catalog API.
- Isolated subprocess execution, logical-path enforcement, production policy, redaction, and secret providers.
- Atomic artifact storage, digests, immutable package verification, and consistent SQLite backup and restore.
- Project and environment selection, project import/export, migration, and explicit customer-data recovery.
- Development project skills. Keep one project scope and one small built-in scope; do not execute custom import-time code in the parent process.
- Browser, files, and the one report transformer required by the proof workflow.
- One immutable Python automation publisher and runner after its contract is proved.
- One macOS and one Windows delivery path after their native prototypes pass.

## Ranked deletion inventory

Counts are gross tracked lines in complete named files. Replacement lines are not subtracted unless stated.

| Rank | Action | Exact scope | Measured cut | Gate |
|---:|---|---|---:|---|
| 1 | Delete | Model-specific adaptive Codex loop: `adaptive.py`, `codex_provider.py`, and `test_adaptive_codex.py` | 746 lines plus CLI, application, and README glue | None. External AI clients already drive the typed contract. |
| 2 | Delete or trim | `.pi/prompts/`, unused root `skills/SKILL.md`, stale external-AI plan and handoff, and completed Pi research | 1,878 lines | Keep `AGENTS.md`, the operator guide, live manifest, and primary-source security evidence. |
| 3 | Replace, then delete | Legacy installer package, shell and PowerShell bootstraps, bootstrap tests, wheelhouse bundle builder, and bundle test | 4,176 lines | First prove signatures, artifact hashes, secret-free output, failure before activation, install, update, rollback, browser launch, data preservation, and data-preserving uninstall. Rewrite `prepare_release_candidate.py` and its test to build native payloads; their lines are not counted as deleted. |
| 4 | Decide, then delete | Local observer web application and its test; retain the read-only ActiveGraph adapter for agent reads | 3,410 lines | Confirm that typed run, event, trace, artifact, and diagnostic operations are the v1 observer surface. |
| 5 | Replace, then delete | JSON recipe runtime and its focused tests | 1,930 lines gross | The immutable Python automation prototype and customer-state migration decision must pass. A 200–300 line executor gives about 1,600–1,700 net lines removed. |
| 6 | Replace, then shrink | Phase handoff copies, phase machine, reconciliation wrappers, and focused tests | 1,378 lines gross | Keep fixed development stages, automation-phase events, artifact digests, retry lineage, interrupted-write recovery, and equivalent artifact and handoff reconciliation. Decide the canonical step evidence first. |
| 7 | Delete after proof scope | HTTP, feedback, and shell skills plus the HTTP test | 359 lines | Keep Excel and PDF until the proof workflow and `product-direction.md` change together. Shell is not an OS sandbox. PDF must gain its missing qualified dependency while it remains core. |
| 8 | Replace, then delete | Detached JSON task subsystem used only by store backup | 507 lines | Prove synchronous backup stays within the agent time limit and keeps stable retry results. |
| 9 | Generate, then delete | Hand-written operation schema table and its duplicate validation test | 522 lines gross | The model-neutral operation contract must generate JSON Schema from operation-specific Pydantic models. This can remove `jsonschema` and its direct type package. |
| 10 | Reduce | Human CLI parser, aliases, duplicate handlers, and operations outside the accepted customer journey | Not counted yet | Keep a thin `agent`, `version`, `doctor`, `update`, and rollback entry point. Fix the operation contract and customer-data surface, then record an exact symbol list before removal. |
| 11 | Delete after native delivery | Obsolete release plan and release handoff after valid security and migration rules move into `distribution-and-updates.md` | 1,807 lines gross | Native delivery and managed browser requirements must remain authoritative. |

## Skill boundary

The tracked skill tree has 25 tools and 1,742 lines.

- Keep browser navigation, inspection, fill/click, download, screenshot evidence, and session internals only when the proof portal uses them.
- Keep minimal file read, write, copy, and glob operations until the proof fixes the smaller set. Artifact attachment already supplies hashing.
- Keep Excel and PDF because the current product direction names both. If the proof does not use one, change the product direction and skill set in the same decision.
- Remove generic HTTP, feedback, and shell from core unless a named workflow proves a need.
- Do not restore the 15 removed connector directories. They contain no tracked source.
- Keep project skill development, but move custom schema inspection into a bounded subprocess. The current loader imports custom script code in the parent process.

## Dependencies

The root has five direct runtime dependencies.

- Keep `activegraph`, `playwright`, and `pydantic`.
- Pin Playwright to the exact qualified version or enforce the same exact release constraint.
- Remove `jsonschema` and `types-jsonschema` after Pydantic becomes the only operation-schema source.
- Remove `openpyxl` and `et-xmlfile` only if both Excel execution and workbook preview leave core.
- Remove `build` and `twine` after native packages replace source-distribution and PyPI checks.
- Delete the separate installer lock and environment after native package acceptance.

## Rejected cuts

These cuts would create debt or weaken the accepted destination.

- Do not delete project import/export, migration, online backup, staged restore, or secret migration. Customer state must survive pre-1.0 reduction.
- Do not call ActiveGraph private tool methods to save adapter lines.
- Do not replace the read-only projection adapter with an ActiveGraph open path that can write schema or WAL state.
- Do not remove project skills. Skills are required for development exploration; remove only the extra home-shared scope if the operation-contract decision permits it.
- Do not execute unrestricted automation Python. It must use the typed run context and existing path, secret, approval, idempotency, and policy controls.
- Do not remove the legacy installer until both native journeys pass on clean systems.
- Do not remove migration and storage contracts before the missing version-3 to version-4 migration exists and passes interruption and rollback checks.

## Projected result

The present application, skill, script, and installer implementation is 25,518 tracked lines before tests and documents. The high-confidence route removes about 13,000–15,000 gross lines. The Python runner and native package work will add code, so the realistic target is 11,000–14,000 implementation lines, a projected 45–57% reduction.

This is a target, not completion evidence. The final number must be measured after the proof workflow passes on macOS and Windows.

## Required order

1. Remove model-specific orchestration and duplicate guidance that already has an accepted replacement.
2. Decide the observer and customer-data surfaces.
3. Prove the immutable Python automation contract.
4. Fix the typed operation and skill contract.
5. Define migration and implement the missing storage migration checks.
6. Prove the macOS and Windows native delivery paths.
7. Add one installed browser-file-artifact-ship-production acceptance journey.
8. Remove replaced code and optional skills.
9. Run release qualification and measure the final tree.

Net: about 13,000–15,000 gross implementation lines and at least two direct contract dependencies can be removed, subject to the listed replacement gates. Recalculate the net after the Python runner and native packages exist.
