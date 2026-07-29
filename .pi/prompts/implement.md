---
description: Implement an ROI-H change with scout, plan, tests, implementation, and review
argument-hint: "<change>"
---
Implement this ROI-H change: $ARGUMENTS

Workflow:
1. Inspect the relevant code, tests, AGENTS.md, and current git diff.
2. Use the `scout` subagent for fast repository recon and the `planner` subagent for a concrete plan when the change is not trivial.
3. Follow test-first development at the public interface seam. Add one focused failing test before implementation.
4. Implement the smallest complete change. Preserve the typed ROI-H agent contract and logical storage boundaries.
5. Run focused tests, lint, and type checks for the changed area.
6. Use parallel `reviewer` subagents for correctness, tests, and unnecessary complexity.
7. Apply valid review fixes, then report changed files and verification results.

Do not hand-edit generated recipes or committed automation packages. Do not expose secrets.
