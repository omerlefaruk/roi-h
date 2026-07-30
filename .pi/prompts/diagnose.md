---
description: Diagnose an ROI-H failure with evidence before changing code
argument-hint: "<failure>"
---
Diagnose this ROI-H failure: $ARGUMENTS

Use this loop:
1. Reproduce the failure with the smallest public command or test.
2. Inspect the structured error, request ID, run ID, task ID, events, artifacts, and diagnostics.
3. Use a `scout` subagent to inspect the likely code path and a `researcher` subagent only when an external fact is required.
4. State the failing invariant and the smallest seam where the defect lives.
5. Add a regression test at that seam before changing implementation.
6. Fix the cause, not only the symptom.
7. Run the focused test and related tests. Report evidence, root cause, fix, and remaining risk.

Do not retry an uncertain destructive operation with a new idempotency key.
