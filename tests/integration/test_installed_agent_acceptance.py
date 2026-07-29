"""Installed-wheel acceptance for the CLI-only external AI interface."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any
from uuid import uuid4

_REPO_ROOT = Path(__file__).parents[2]


def test_installed_cli_agent_acceptance_from_empty_home(tmp_path: Path) -> None:
    wheel_dir = tmp_path / "wheel"
    wheel_dir.mkdir()
    build = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--outdir",
            str(wheel_dir),
            str(_REPO_ROOT),
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert build.returncode == 0, build.stdout + build.stderr

    environment = tmp_path / "installed"
    uv = shutil.which("uv")
    assert uv is not None
    create_environment = subprocess.run(  # noqa: S603
        [
            uv,
            "venv",
            "--system-site-packages",
            "--python",
            sys.executable,
            str(environment),
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert create_environment.returncode == 0, (
        create_environment.stdout + create_environment.stderr
    )
    python = environment / "bin" / "python"
    install = subprocess.run(  # noqa: S603
        [
            uv,
            "pip",
            "install",
            "--python",
            str(python),
            str(next(wheel_dir.glob("roi_h-*.whl"))),
        ],
        cwd=tmp_path,
        text=True,
        capture_output=True,
        check=False,
    )
    assert install.returncode == 0, install.stdout + install.stderr
    executable = environment / "bin" / "roi-h"
    home = tmp_path / "empty-home"
    isolated_cwd = tmp_path / "agent-work"
    isolated_cwd.mkdir()
    clean_environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONPATH", "PYTHONHOME", "ROI_H_HOME"}
    }

    described = _run(executable, isolated_cwd, clean_environment, "agent", "describe")
    assert described.returncode == 0, described.stdout
    operations = json.loads(described.stdout)["result"]["operations"]
    assert len(operations) == 83

    created = _call(
        executable,
        isolated_cwd,
        clean_environment,
        "project.create",
        {
            "idempotency_key": "accept-project",
            "arguments": {"home": str(home), "name": "acceptance"},
        },
    )
    assert created["ok"] is True

    context = _run(
        executable,
        isolated_cwd,
        clean_environment,
        "agent",
        "context",
        "--home",
        str(home),
    )
    assert json.loads(context.stdout)["result"]["project"] == "acceptance"

    base = {
        "context": {"project": "acceptance", "environment": "dev"},
        "arguments": {"home": str(home)},
    }
    started = _call(
        executable,
        isolated_cwd,
        clean_environment,
        "run.start",
        {
            **base,
            "idempotency_key": "accept-run",
            "arguments": {
                **base["arguments"],
                "run_id": "installed-agent-run",
                "goal": "Qualify the installed CLI",
            },
        },
    )
    assert started["result"]["run_id"] == "installed-agent-run"

    listed = _call(
        executable,
        isolated_cwd,
        clean_environment,
        "run.list",
        {**base, "arguments": {**base["arguments"], "limit": 10}},
    )
    assert listed["result"]["items"][0]["run_id"] == "installed-agent-run"

    phase_context = {
        **base,
        "context": {
            "project": "acceptance",
            "environment": "dev",
            "run_id": "installed-agent-run",
        },
    }
    begun = _call(
        executable,
        isolated_cwd,
        clean_environment,
        "phase.begin",
        {
            **phase_context,
            "idempotency_key": "accept-phase-begin",
            "arguments": {**base["arguments"], "name": "qualification"},
        },
    )
    assert begun["ok"] is True
    ended = _call(
        executable,
        isolated_cwd,
        clean_environment,
        "phase.end",
        {
            **phase_context,
            "idempotency_key": "accept-phase-end",
            "arguments": {**base["arguments"], "summary": {"qualified": True}},
        },
    )
    assert ended["ok"] is True

    secret_request = {
        **base,
        "idempotency_key": "accept-secret",
        "arguments": {**base["arguments"], "name": "TOKEN"},
    }
    secret_value = f"value-{uuid4().hex}"
    secret = _call(
        executable,
        isolated_cwd,
        clean_environment,
        "secret.set",
        secret_request,
        secret_stdin=secret_value,
    )
    assert secret["result"]["name"] == "TOKEN"
    assert secret_value not in json.dumps(secret)

    backup = _call(
        executable,
        isolated_cwd,
        clean_environment,
        "store.backup",
        {
            **base,
            "idempotency_key": "accept-backup",
            "arguments": {
                **base["arguments"],
                "output": str(tmp_path / "activegraph.backup.sqlite"),
            },
        },
    )
    task_id = backup["result"]["task"]["task_id"]
    task = _call(
        executable,
        isolated_cwd,
        clean_environment,
        "task.show",
        {"arguments": {"home": str(home), "task_id": task_id}},
    )
    assert task["result"]["state"] == "succeeded"

    support = _call(
        executable,
        isolated_cwd,
        clean_environment,
        "support_bundle.create",
        {
            **base,
            "idempotency_key": "accept-support",
            "arguments": {
                **base["arguments"],
                "output": str(tmp_path / "support.zip"),
            },
        },
    )
    assert support["result"]["created"] is True
    with zipfile.ZipFile(tmp_path / "support.zip") as archive:
        support_text = "\n".join(
            archive.read(name).decode("utf-8", errors="ignore")
            for name in archive.namelist()
        )
    assert secret_value not in support_text


def _call(  # noqa: PLR0913 - process boundary inputs stay explicit.
    executable: Path,
    cwd: Path,
    environment: dict[str, str],
    operation: str,
    request: dict[str, Any],
    *,
    secret_stdin: str | None = None,
) -> dict[str, Any]:
    request_file = cwd / f"{operation.replace('.', '-')}-{uuid4().hex}.json"
    request_file.write_text(json.dumps(request), encoding="utf-8")
    arguments = ["agent", "call", operation, "--input", str(request_file)]
    if secret_stdin is not None:
        arguments.append("--secret-stdin")
    completed = _run(
        executable,
        cwd,
        environment,
        *arguments,
        stdin=secret_stdin,
    )
    payload = json.loads(completed.stdout)
    assert completed.returncode == 0, payload
    return payload


def _run(
    executable: Path,
    cwd: Path,
    environment: dict[str, str],
    *arguments: str,
    stdin: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        [str(executable), *arguments],
        cwd=cwd,
        env=environment,
        input=stdin,
        text=True,
        capture_output=True,
        check=False,
    )
