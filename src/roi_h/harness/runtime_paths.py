"""Run-scoped filesystem paths shared by skill workers."""

from __future__ import annotations

import os
from pathlib import Path


def run_workspace(
    *,
    home: str | Path,
    project: str,
    env: str,
    run_id: str,
) -> Path:
    """Return the isolated mutable workspace for one automation run."""
    if env not in {"dev", "prod"}:
        msg = f"env must be 'dev' or 'prod', got {env!r}"
        raise ValueError(msg)
    return (
        Path(home).expanduser().resolve()
        / "projects"
        / project
        / "environments"
        / env
        / "runs"
        / run_id
        / "workspace"
        / "work"
    )


def run_workspace_from_environ() -> Path:
    """Resolve the current worker's run workspace from its scoped environment."""
    explicit = os.environ.get("ROI_H_RUN_DIR")
    if explicit:
        return Path(explicit).expanduser().resolve()
    required = {
        name: os.environ.get(name, "").strip()
        for name in ("ROI_H_HOME", "ROI_H_PROJECT", "ROI_H_ENV", "ROI_H_RUN_ID")
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        msg = f"missing run workspace environment: {', '.join(missing)}"
        raise RuntimeError(msg)
    return run_workspace(
        home=required["ROI_H_HOME"],
        project=required["ROI_H_PROJECT"],
        env=required["ROI_H_ENV"],
        run_id=required["ROI_H_RUN_ID"],
    )
