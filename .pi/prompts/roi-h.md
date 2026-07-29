---
description: Use the ROI-H bridge to inspect, plan, and execute typed operations
argument-hint: "<goal>"
---
Use the ROI-H bridge to achieve this goal: $ARGUMENTS

1. Call `roi_h_context` first.
2. Search the operation catalog with `roi_h_search`.
3. Activate typed operations with `roi_h_activate` when their schemas are useful.
4. Use `roi_h_execute` for any operation that is not activated.
5. Preserve the current project and environment context.
6. Use stable idempotency keys for retried writes.
7. Use plan/apply operations for destructive changes.
8. Follow task IDs, event IDs, approvals, and next actions until the operation reaches a terminal state.
9. Never invent secret values. Let `secret.set` request the value from the user.
