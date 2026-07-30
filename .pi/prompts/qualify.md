---
description: Run the smallest useful ROI-H checks and qualify release-impacting changes
argument-hint: "[scope]"
---
Qualify the current ROI-H change. Scope: ${ARGUMENTS:-the current diff}.

1. Inspect the current diff and identify the smallest relevant test set.
2. Run focused unit and integration tests first.
3. Run Ruff and mypy for changed Python modules.
4. Run `uv run python scripts/check_publication_boundary.py` when tracked files or skills changed.
5. Run `uv run python scripts/qualify_release.py` for release, packaging, installer, CLI contract, or publication changes.
6. If a check fails, report the exact command, first useful error, and whether the failure is caused by the current diff.
7. Do not claim success without command output.
