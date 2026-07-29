---
name: feedback
description: >
  After each automation run, record structured feedback so humans/AI can improve
  the harness, skills, and recipes. Invoked automatically by rpa run when present.
version: 0.1.0
---
# feedback

## Tools
| Tool | Notes |
|---|---|
| `feedback.record` | Write a JSONL entry under project feedback/ |
| `feedback.list` | List recent feedback entries |

## When to use
- Automatically after `rpa run` (harness calls `feedback.record` if installed)
- Manually after a messy explore session to capture lessons

## What to capture
- What worked / failed
- Brittle selectors or missing global tools
- Suggested skill or harness changes
