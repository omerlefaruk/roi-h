"""Installed-wheel acceptance for log-based, guidance-skill automation."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).parents[2]


def test_installed_cli_runs_the_complete_modular_automation_lifecycle(  # noqa: PLR0915
    tmp_path: Path,
) -> None:
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
    wheel = next(wheel_dir.glob("roi_h-*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        packaged_skills = [name for name in archive.namelist() if "/_skills/" in name]
    assert packaged_skills
    assert all(name.endswith((".md", "/")) for name in packaged_skills)

    environment = tmp_path / "installed"
    uv = shutil.which("uv")
    assert uv is not None
    created = _process(
        [uv, "venv", "--system-site-packages", "--python", sys.executable, str(environment)],
        cwd=tmp_path,
    )
    assert created.returncode == 0, created.stdout + created.stderr
    scripts = environment / ("Scripts" if os.name == "nt" else "bin")
    python = scripts / ("python.exe" if os.name == "nt" else "python")
    installed = _process(
        [uv, "pip", "install", "--python", str(python), str(wheel)],
        cwd=tmp_path,
    )
    assert installed.returncode == 0, installed.stdout + installed.stderr

    executable = scripts / ("roi-h.exe" if os.name == "nt" else "roi-h")
    work = tmp_path / "agent-work"
    work.mkdir()
    home = tmp_path / "home"
    clean_environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"PYTHONPATH", "PYTHONHOME", "ROI_H_HOME"}
    }

    described = _process([str(executable), "agent", "describe"], cwd=work, env=clean_environment)
    assert described.returncode == 0, described.stdout + described.stderr
    operation_ids = {
        item["operation_id"] for item in json.loads(described.stdout)["result"]["operations"]
    }
    assert "automation.source.put" in operation_ids
    assert "automation.dev.run" in operation_ids
    assert "tool.invoke" not in operation_ids
    assert "skill.define" not in operation_ids

    project = _call(
        executable,
        work,
        clean_environment,
        "project.create",
        {
            "idempotency_key": "accept-project",
            "arguments": {"home": str(home), "name": "acceptance"},
        },
    )
    assert project["ok"] is True

    dev = {
        "context": {"project": "acceptance", "environment": "dev"},
        "arguments": {"home": str(home)},
    }
    skills = _call(executable, work, clean_environment, "skill.list", dev)
    assert {item["name"] for item in skills["result"]["items"]} >= {
        "browser",
        "excel",
        "files",
        "pdf",
    }
    files_skill = _call(
        executable,
        work,
        clean_environment,
        "skill.show",
        {**dev, "arguments": {**dev["arguments"], "name": "files"}},
    )
    assert files_skill["result"]["valid"] is True
    assert files_skill["result"]["documents"] == ["SKILL.md"]

    source = _call(
        executable,
        work,
        clean_environment,
        "automation.source.put",
        {
            **dev,
            "idempotency_key": "accept-source",
            "arguments": {
                **dev["arguments"],
                "name": "report",
                "manifest": {
                    "name": "report",
                    "max_parallel": 2,
                    "phases": [
                        {
                            "id": "left",
                            "module": "phases.left",
                            "parallel_safe": True,
                        },
                        {
                            "id": "right",
                            "module": "phases.right",
                            "parallel_safe": True,
                        },
                        {
                            "id": "verify",
                            "module": "phases.verify",
                            "role": "verify",
                            "needs": ["left", "right"],
                        },
                    ],
                },
                "files": _phase_files(),
            },
        },
    )
    assert source["result"]["files"] == [
        "automation.json",
        "phases/left.py",
        "phases/right.py",
        "phases/verify.py",
    ]

    development = _call(
        executable,
        work,
        clean_environment,
        "automation.dev.run",
        {
            **dev,
            "idempotency_key": "accept-dev-run",
            "arguments": {
                **dev["arguments"],
                "name": "report",
                "run_id": "accept-dev",
            },
        },
    )
    assert development["result"]["status"] == "completed"
    assert development["result"]["verification_ok"] is True

    shipped = _call(
        executable,
        work,
        clean_environment,
        "automation.ship",
        {
            **dev,
            "idempotency_key": "accept-ship",
            "arguments": {
                **dev["arguments"],
                "name": "report",
                "version": "1.0.0",
                "from_run": "accept-dev",
            },
        },
    )
    assert shipped["result"]["package_digest"].startswith("sha256:")

    prod = {
        "context": {"project": "acceptance", "environment": "prod"},
        "arguments": {"home": str(home)},
    }
    production = _call(
        executable,
        work,
        clean_environment,
        "automation.run",
        {
            **prod,
            "idempotency_key": "accept-prod-run",
            "arguments": {
                **prod["arguments"],
                "name": "report",
                "run_id": "accept-prod",
            },
        },
    )
    assert production["result"]["status"] == "completed"
    assert (
        production["result"]["automation"]["package_digest"] == shipped["result"]["package_digest"]
    )

    events = _call(
        executable,
        work,
        clean_environment,
        "run.events",
        {
            "context": {**prod["context"], "run_id": "accept-prod"},
            "arguments": {"home": str(home), "run_id": "accept-prod", "limit": 50},
        },
    )
    event_types = {item["type"] for item in events["result"]["items"]}
    assert {"source.snapshotted", "phase.succeeded", "run.completed"} <= event_types


def _phase_files() -> dict[str, str]:
    worker = (
        "def run(context):\n"
        "    path = context.output_path(context.phase_id + '.txt')\n"
        "    path.write_text(context.phase_id, encoding='utf-8')\n"
        "    return {'artifacts': {context.phase_id: context.phase_id + '.txt'}}\n"
    )
    verify = (
        "def run(context):\n"
        "    left = context.dependencies['left']['left'].read_text(encoding='utf-8')\n"
        "    right = context.dependencies['right']['right'].read_text(encoding='utf-8')\n"
        "    assert left + right == 'leftright'\n"
        "    return {'summary': {'verified': True}}\n"
    )
    return {
        "phases/left.py": worker,
        "phases/right.py": worker,
        "phases/verify.py": verify,
    }


def _call(
    executable: Path,
    cwd: Path,
    environment: dict[str, str],
    operation: str,
    request: dict[str, Any],
) -> dict[str, Any]:
    completed = _process(
        [str(executable), "agent", "call", operation, "--input", "-"],
        cwd=cwd,
        env=environment,
        stdin=json.dumps(request),
    )
    payload = json.loads(completed.stdout)
    assert completed.returncode == 0, payload
    return payload


def _process(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None = None,
    stdin: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        command,
        cwd=cwd,
        env=env,
        input=stdin,
        text=True,
        capture_output=True,
        check=False,
    )
