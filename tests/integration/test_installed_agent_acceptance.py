"""Installed-wheel acceptance for the CLI-only external AI interface."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

_REPO_ROOT = Path(__file__).parents[2]


def test_installed_cli_agent_acceptance_from_empty_home(  # noqa: PLR0915
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
    assert create_environment.returncode == 0, create_environment.stdout + create_environment.stderr
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
    tools = _call(
        executable,
        isolated_cwd,
        clean_environment,
        "tool.list",
        {**base, "arguments": {**base["arguments"], "limit": 20}},
    )
    assert tools["result"]["items"]
    shown_tool = _call(
        executable,
        isolated_cwd,
        clean_environment,
        "tool.show",
        {**base, "arguments": {**base["arguments"], "name": "files.read"}},
    )
    assert shown_tool["result"]["input_schema"]["type"] == "object"

    read_source = _skill_source("inspect", effect="read", approval=False)
    write_source = _skill_source("echo", effect="write", approval=True)
    for tool_name, source in (("inspect", read_source), ("echo", write_source)):
        defined = _call(
            executable,
            isolated_cwd,
            clean_environment,
            "skill.define",
            {
                **base,
                "idempotency_key": f"accept-define-{tool_name}",
                "arguments": {
                    **base["arguments"],
                    "skill": "sample",
                    "tool": tool_name,
                    "description": f"Acceptance {tool_name}",
                    "source": source,
                },
            },
        )
        assert defined["result"]["name"] == f"sample.{tool_name}"
    validated = _call(
        executable,
        isolated_cwd,
        clean_environment,
        "skill.validate",
        {**base, "arguments": {**base["arguments"], "name": "sample"}},
    )
    assert validated["result"]["valid"] is True

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

    input_source = isolated_cwd / "input.txt"
    input_source.write_text("installed agent input", encoding="utf-8")
    added_input = _call(
        executable,
        isolated_cwd,
        clean_environment,
        "run.input.add",
        {
            **base,
            "idempotency_key": "accept-input",
            "context": {
                "project": "acceptance",
                "environment": "dev",
                "run_id": "installed-agent-run",
            },
            "arguments": {
                **base["arguments"],
                "source": str(input_source),
                "name": "input.txt",
            },
        },
    )
    assert added_input["result"]["path"] == "run://input/input.txt"

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

    read_step = _call(
        executable,
        isolated_cwd,
        clean_environment,
        "tool.invoke",
        {
            **phase_context,
            "idempotency_key": "accept-read-invoke",
            "arguments": {
                **base["arguments"],
                "name": "sample.inspect",
                "arguments": {"value": "read result"},
                "force": True,
            },
        },
    )
    assert read_step["result"]["status"] == "ok", json.dumps(read_step, indent=2)
    pending = _call(
        executable,
        isolated_cwd,
        clean_environment,
        "tool.invoke",
        {
            **phase_context,
            "idempotency_key": "accept-write-invoke",
            "arguments": {
                **base["arguments"],
                "name": "sample.echo",
                "arguments": {"value": "write result"},
            },
        },
    )
    assert pending["result"]["status"] == "pending_approval"
    approved = _call(
        executable,
        isolated_cwd,
        clean_environment,
        "approval.approve",
        {
            **phase_context,
            "idempotency_key": "accept-approve",
            "arguments": {
                **base["arguments"],
                "approval_id": pending["result"]["approval_id"],
                "by": "acceptance",
            },
        },
    )
    assert approved["result"]["status"] == "ok"
    pending_reject = _call(
        executable,
        isolated_cwd,
        clean_environment,
        "tool.invoke",
        {
            **phase_context,
            "idempotency_key": "accept-write-reject",
            "arguments": {
                **base["arguments"],
                "name": "sample.echo",
                "arguments": {"value": "do not run"},
            },
        },
    )
    rejected = _call(
        executable,
        isolated_cwd,
        clean_environment,
        "approval.reject",
        {
            **phase_context,
            "idempotency_key": "accept-reject",
            "arguments": {
                **base["arguments"],
                "approval_id": pending_reject["result"]["approval_id"],
                "by": "acceptance",
                "reason": "qualification rejection",
            },
        },
    )
    assert rejected["result"]["status"] == "denied"

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

    trace = _call(
        executable,
        isolated_cwd,
        clean_environment,
        "run.trace",
        {
            **phase_context,
            "arguments": {**base["arguments"], "limit": 200},
        },
    )
    assert trace["result"]["steps"]
    event_page = _call(
        executable,
        isolated_cwd,
        clean_environment,
        "run.events",
        {
            **phase_context,
            "arguments": {**base["arguments"], "limit": 1},
        },
    )
    if event_page["result"]["has_more"]:
        resumed_events = _call(
            executable,
            isolated_cwd,
            clean_environment,
            "run.events",
            {
                **phase_context,
                "arguments": {
                    **base["arguments"],
                    "limit": 200,
                    "after": event_page["result"]["next_cursor"],
                },
            },
        )
        assert resumed_events["result"]["items"]

    artifact_source = isolated_cwd / "artifact.txt"
    artifact_source.write_text("installed artifact", encoding="utf-8")
    artifact = _call(
        executable,
        isolated_cwd,
        clean_environment,
        "artifact.put",
        {
            **phase_context,
            "idempotency_key": "accept-artifact",
            "arguments": {
                **base["arguments"],
                "source": str(artifact_source),
                "name": "artifact.txt",
            },
        },
    )
    artifact_id = artifact["result"]["artifact_id"]
    exported_artifact = isolated_cwd / "artifact-export.txt"
    exported = _call(
        executable,
        isolated_cwd,
        clean_environment,
        "artifact.export",
        {
            **phase_context,
            "idempotency_key": "accept-artifact-export",
            "arguments": {
                **base["arguments"],
                "artifact_id": artifact_id,
                "output": str(exported_artifact),
            },
        },
    )
    assert exported_artifact.read_text(encoding="utf-8") == "installed artifact"
    assert exported["result"]["sha256"] == artifact["result"]["sha256"]

    promoted = _call(
        executable,
        isolated_cwd,
        clean_environment,
        "skill.promote",
        {
            **base,
            "idempotency_key": "accept-promote",
            "arguments": {**base["arguments"], "name": "sample"},
        },
    )
    assert promoted["result"]["name"] == "sample"

    for version in ("1.0.0", "1.0.1"):
        shipped = _call(
            executable,
            isolated_cwd,
            clean_environment,
            "automation.ship",
            {
                **base,
                "idempotency_key": f"accept-ship-{version}",
                "arguments": {
                    **base["arguments"],
                    "name": "accepted-job",
                    "version": version,
                    "from_run": "installed-agent-run",
                    "distill": True,
                },
            },
        )
        assert shipped["result"]["shipped"] is True
    for operation in ("automation.show", "automation.verify"):
        inspected = _call(
            executable,
            isolated_cwd,
            clean_environment,
            operation,
            {
                **base,
                "arguments": {
                    **base["arguments"],
                    "name": "accepted-job",
                    "version": "1.0.1",
                },
            },
        )
        assert inspected["result"]["package_digest"]
    compared = _call(
        executable,
        isolated_cwd,
        clean_environment,
        "automation.compare",
        {
            **base,
            "arguments": {
                **base["arguments"],
                "name": "accepted-job",
                "version_a": "1.0.0",
                "version_b": "1.0.1",
            },
        },
    )
    assert compared["result"]["version_a"] == "1.0.0"
    prod = {
        "context": {"project": "acceptance", "environment": "prod"},
        "arguments": {"home": str(home)},
    }
    for run_id, dry_run in (("accepted-dry", True), ("accepted-live", False)):
        automated = _call(
            executable,
            isolated_cwd,
            clean_environment,
            "automation.run",
            {
                **prod,
                "idempotency_key": f"accept-automation-{run_id}",
                "arguments": {
                    **prod["arguments"],
                    "name": "accepted-job",
                    "version": "1.0.1",
                    "run_id": run_id,
                    "dry_run": dry_run,
                    "auto_approve": True,
                    "force": True,
                },
            },
        )
        assert automated["result"]["run_id"] == run_id
        if dry_run:
            assert automated["result"]["dry_run"] is True
        else:
            assert automated["result"]["ok"] is True

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
    assert str(tmp_path) not in json.dumps(backup)
    task = _call(
        executable,
        isolated_cwd,
        clean_environment,
        "task.show",
        {"arguments": {"home": str(home), "task_id": task_id}},
    )
    assert task["result"]["state"] == "succeeded"
    first_task_events = _call(
        executable,
        isolated_cwd,
        clean_environment,
        "task.events",
        {"arguments": {"home": str(home), "task_id": task_id, "limit": 1}},
    )
    resumed_task_events = _call(
        executable,
        isolated_cwd,
        clean_environment,
        "task.events",
        {
            "arguments": {
                "home": str(home),
                "task_id": task_id,
                "limit": 20,
                "after": first_task_events["result"]["items"][0]["event_id"],
            }
        },
    )
    assert resumed_task_events["result"]["items"][-1]["type"] == "task.succeeded"
    waited = _call(
        executable,
        isolated_cwd,
        clean_environment,
        "task.wait",
        {
            "arguments": {
                "home": str(home),
                "task_id": task_id,
                "timeout_seconds": 1,
            }
        },
    )
    assert waited["result"]["state"] == "succeeded"
    cancelled = _call(
        executable,
        isolated_cwd,
        clean_environment,
        "task.cancel",
        {
            "arguments": {
                "home": str(home),
                "task_id": task_id,
            }
        },
    )
    assert cancelled["result"]["state"] == "succeeded"
    checked_store = _call(
        executable,
        isolated_cwd,
        clean_environment,
        "store.check",
        {**base, "arguments": {**base["arguments"], "full": True}},
    )
    assert checked_store["result"]["ok"] is True
    assert str(tmp_path) not in json.dumps(checked_store)

    delete_plan = _call(
        executable,
        isolated_cwd,
        clean_environment,
        "project.delete.plan",
        {
            **base,
            "arguments": {**base["arguments"], "name": "acceptance"},
        },
    )
    _call(
        executable,
        isolated_cwd,
        clean_environment,
        "skill.define",
        {
            **base,
            "idempotency_key": "accept-stale-marker",
            "arguments": {
                **base["arguments"],
                "skill": "stale",
                "tool": "marker",
            },
        },
    )
    stale = _call(
        executable,
        isolated_cwd,
        clean_environment,
        "project.delete.apply",
        {
            **base,
            "idempotency_key": "accept-stale-apply",
            "arguments": {
                **base["arguments"],
                "plan_id": delete_plan["result"]["plan_id"],
            },
        },
        expected_exit=1,
    )
    assert stale["error"]["code"] == "plan.state_changed"
    assert stale["changed"] is False

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
            archive.read(name).decode("utf-8", errors="ignore") for name in archive.namelist()
        )
    assert secret_value not in support_text
    event_text = json.dumps(trace) + json.dumps(event_page)
    assert secret_value not in event_text
    for path in home.rglob("*"):
        if not path.is_file():
            continue
        assert secret_value.encode() not in path.read_bytes()


def _call(  # noqa: PLR0913 - process boundary inputs stay explicit.
    executable: Path,
    cwd: Path,
    environment: dict[str, str],
    operation: str,
    request: dict[str, Any],
    *,
    secret_stdin: str | None = None,
    expected_exit: int = 0,
) -> dict[str, Any]:
    request_file = cwd / f"{operation.replace('.', '-')}-{uuid4().hex}.json"
    request_file.write_text(json.dumps(request), encoding="utf-8")
    arguments = ["agent", "call", operation, "--input", str(request_file)]
    if secret_stdin is not None:
        arguments.append("--secret-stdin")
        assert secret_stdin not in arguments
    completed = _run(
        executable,
        cwd,
        environment,
        *arguments,
        stdin=secret_stdin,
    )
    payload = cast("dict[str, Any]", json.loads(completed.stdout))
    assert completed.returncode == expected_exit, payload
    return payload


def _skill_source(tool_id: str, *, effect: str, approval: bool) -> str:
    return (
        "from pydantic import BaseModel\n"
        f"TOOL_ID={tool_id!r}\n"
        f"TOOL_EFFECT={effect!r}\n"
        f"REQUIRES_APPROVAL={approval!r}\n"
        "ALLOW_IN_PROD=True\n"
        "IDEMPOTENCY='key'\n"
        "class Input(BaseModel):\n"
        "    value: str = ''\n"
        "class Output(BaseModel):\n"
        "    ok: bool = True\n"
        "    result: str = ''\n"
        "def run(args: Input) -> Output:\n"
        "    return Output(ok=True, result=args.value)\n"
    )


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
