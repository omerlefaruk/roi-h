---
description: Review the current ROI-H diff with parallel defect-focused reviewers
argument-hint: "[focus]"
---
Review the current ROI-H diff. Focus: ${ARGUMENTS:-correctness, security, contract compatibility, tests, and unnecessary complexity}.

1. Inspect `git diff`, the changed public interfaces, and related tests.
2. Run parallel `reviewer` subagents. Use separate focus areas for correctness, security and secret handling, contract/schema compatibility, and test coverage.
3. Report only actionable findings, ordered by severity.
4. For every finding, give the file, location, failure mode, and a concrete fix.
5. Check that tests exercise public interfaces rather than implementation details.
6. Do not modify files unless the user asks for fixes.
