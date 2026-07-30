"""Load Claude-style skills and bind their scripts as ActiveGraph tools."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

from activegraph import Tool
from pydantic import BaseModel, ConfigDict

from roi_h.harness.domain import IdempotencyMode, SkillScope, ToolEffect, ToolInfo
from roi_h.harness.runtime_environment import isolated_process_environment
from roi_h.harness.skill_contract import SkillInspection, inspect_module, skill_tree_digest
from roi_h.harness.workspace import resolve_home

if TYPE_CHECKING:
    from activegraph.tools import ToolContext

_SCRIPT_GLOBALS_PREFIX = "roi_h_skill_"
_SKILL_NAME_RE_OK = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
_INSPECTION_TIMEOUT_SECONDS = 10.0


class _IsolatedInput(BaseModel):
    """Parent-owned object adapter; the worker performs exact validation."""

    model_config = ConfigDict(extra="allow", strict=True)


class _IsolatedOutput(_IsolatedInput):
    pass


def default_skills_root() -> Path:
    """Resolve packaged skills first, then the source-checkout catalog."""
    packaged = Path(__file__).resolve().parents[1] / "_skills"
    if packaged.is_dir():
        return packaged
    # src/roi_h/harness/loader.py → parents[3] == repository root
    return Path(__file__).resolve().parents[3] / "skills"


def resolve_skills_root(explicit: str | Path | None = None) -> Path:
    """Prefer an explicit root, then ``ROI_H_SKILLS``, then the repo default."""
    if explicit is not None:
        return Path(explicit).expanduser().resolve()
    env = os.environ.get("ROI_H_SKILLS")
    if env:
        return Path(env).expanduser().resolve()
    return default_skills_root().resolve()


def default_project_skills_root(database: str | Path | None = None) -> Path:
    """Project-local skills live beside the run DB: ``<db-parent>/skills``.

    Prefer paths from ``Workspace.project_skills`` in CLI/harness code. This
    helper is a fallback when only a database path (or ``ROI_H_PROJECT_SKILLS``)
    is available.
    """
    if database is not None:
        return Path(database).expanduser().resolve().parent / "skills"
    env = os.environ.get("ROI_H_PROJECT_SKILLS")
    if env:
        return Path(env).expanduser().resolve()
    # Best-effort without a Workspace: active project is not resolved here.
    return (resolve_home() / "projects" / "default" / "dev" / "skills").resolve()


def resolve_project_skills_root(
    explicit: str | Path | None = None,
    *,
    database: str | Path | None = None,
) -> Path:
    """Resolve the project-local skills root (may not exist yet)."""
    if explicit is not None:
        return Path(explicit).expanduser().resolve()
    return default_project_skills_root(database)


@dataclass(frozen=True)
class SkillTool:
    """One script-backed tool discovered under a skill folder."""

    skill: str
    tool_id: str
    description: str
    scope: SkillScope
    requires_approval: bool
    deterministic: bool
    effect: ToolEffect
    idempotency: IdempotencyMode
    allow_in_prod: bool
    timeout_seconds: float
    secret_names: tuple[str, ...]
    network_hosts: tuple[str, ...]
    filesystem_roots: tuple[str, ...]
    script_path: Path
    script_sha256: str
    skill_tree_sha256: str
    reject_bytecode: bool
    skills_root: Path
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    run: Callable[[BaseModel], BaseModel] | None

    @property
    def name(self) -> str:
        """Canonical ``skill.tool_id`` name."""
        return f"{self.skill}.{self.tool_id}"

    def to_info(self) -> ToolInfo:
        """Build the AI-facing catalog row for this tool."""
        return ToolInfo(
            name=self.name,
            skill=self.skill,
            tool_id=self.tool_id,
            description=self.description,
            scope=self.scope,
            requires_approval=self.requires_approval,
            deterministic=self.deterministic,
            effect=self.effect,
            idempotency=self.idempotency,
            allow_in_prod=self.allow_in_prod,
            timeout_seconds=self.timeout_seconds,
            secret_names=list(self.secret_names),
            network_hosts=list(self.network_hosts),
            filesystem_roots=list(self.filesystem_roots),
            script_path=str(self.script_path),
            input_schema=self.input_schema,
            output_schema=self.output_schema,
        )

    def to_activegraph_tool(self) -> Tool:
        """Wrap the skill script as an ActiveGraph ``Tool`` (runtime binder only)."""
        input_model = self.input_model
        output_model = self.output_model
        run = self.run

        def _fn(arguments: BaseModel, context: ToolContext) -> BaseModel:
            del context
            if run is None:
                msg = "custom skill tools execute only through the isolated ROI-H invoker"
                raise RuntimeError(msg)
            if not isinstance(arguments, input_model):
                arguments = input_model.model_validate(
                    arguments.model_dump() if isinstance(arguments, BaseModel) else arguments
                )
            result = run(arguments)
            if not isinstance(result, output_model):
                result = output_model.model_validate(
                    result.model_dump() if isinstance(result, BaseModel) else result
                )
            return result

        _fn.__name__ = self.tool_id
        _fn.__qualname__ = self.name
        return Tool(
            name=self.name,
            fn=_fn,
            description=self.description,
            input_schema=input_model,
            output_schema=output_model,
            deterministic=self.deterministic,
            timeout_seconds=self.timeout_seconds,
        )


@dataclass(frozen=True)
class SkillCatalog:
    """Merged core, shared, and project tools (later scopes win on name clash)."""

    global_root: Path
    shared_root: Path | None
    project_root: Path | None
    tools: tuple[SkillTool, ...]

    @property
    def root(self) -> Path:
        """Backward-compatible primary root (global catalog)."""
        return self.global_root

    def list_tools(self) -> list[ToolInfo]:
        """Return catalog entries for every loaded skill tool."""
        return [tool.to_info() for tool in self.tools]

    def project_tools(self) -> list[SkillTool]:
        """Tools that live only in the project-local skills tree."""
        return [tool for tool in self.tools if tool.scope == "project"]

    def get(self, name: str) -> SkillTool:
        """Look up a tool by canonical ``skill.id`` or unambiguous short id."""
        by_name = {tool.name: tool for tool in self.tools}
        if name in by_name:
            return by_name[name]
        short = [tool for tool in self.tools if tool.tool_id == name]
        if len(short) == 1:
            return short[0]
        if len(short) > 1:
            msg = f"ambiguous short tool name {name!r}; use one of {[t.name for t in short]}"
            raise KeyError(msg)
        msg = f"unknown skill tool {name!r}; known: {sorted(by_name)}"
        raise KeyError(msg)

    def resolve(self, skill: str, tool_id: str) -> SkillTool:
        """Resolve ``skill`` + ``tool_id`` to a loaded tool."""
        return self.get(f"{skill}.{tool_id}")

    def to_activegraph_tools(self) -> list[Tool]:
        """Materialize ActiveGraph binders for every skill tool."""
        return [tool.to_activegraph_tool() for tool in self.tools]


def load_skills(
    root: str | Path | None = None,
    *,
    shared_root: str | Path | None = None,
    project_root: str | Path | None = None,
    database: str | Path | None = None,
) -> SkillCatalog:
    """Load core, optional shared, then project skills in override order."""
    global_root = resolve_skills_root(root)
    if not global_root.is_dir():
        msg = f"global skills root does not exist: {global_root}"
        raise FileNotFoundError(msg)

    resolved_project = resolve_project_skills_root(project_root, database=database)

    by_name: dict[str, SkillTool] = {}
    for tool in _discover_tools(global_root, scope="global"):
        by_name[tool.name] = tool

    resolved_shared = Path(shared_root).expanduser().resolve() if shared_root is not None else None
    if resolved_shared is not None and resolved_shared.is_dir():
        for tool in _discover_tools(resolved_shared, scope="shared"):
            by_name[tool.name] = tool  # shared wins over core

    project_dir = resolved_project
    if project_dir is not None and project_dir.is_dir():
        for tool in _discover_tools(project_dir, scope="project"):
            by_name[tool.name] = tool  # project wins

    tools = tuple(sorted(by_name.values(), key=lambda item: item.name))
    return SkillCatalog(
        global_root=global_root,
        shared_root=resolved_shared,
        project_root=project_dir,
        tools=tools,
    )


def _discover_tools(skills_root: Path, *, scope: SkillScope) -> list[SkillTool]:
    found: list[SkillTool] = []
    for skill_dir in sorted(path for path in skills_root.iterdir() if path.is_dir()):
        if not (skill_dir / "SKILL.md").is_file():
            continue
        skill_name = skill_dir.name
        scripts_dir = skill_dir / "scripts"
        if not scripts_dir.is_dir():
            continue
        for script_path in sorted(scripts_dir.glob("*.py")):
            if script_path.name.startswith("_"):
                continue
            found.append(
                _load_script_tool(
                    skill=skill_name,
                    script_path=script_path,
                    scope=scope,
                    skills_root=skills_root,
                )
            )
    return found


def _load_script_tool(
    *,
    skill: str,
    script_path: Path,
    scope: SkillScope,
    skills_root: Path,
) -> SkillTool:
    resolved = script_path.resolve()
    script_sha256 = hashlib.sha256(resolved.read_bytes()).hexdigest()
    skill_root = resolved.parent.parent
    trusted_source = _trusted_source(resolved)
    reject_bytecode = trusted_source is None
    tree_sha256 = skill_tree_digest(skill_root, reject_bytecode=reject_bytecode)
    source = resolved.read_text(encoding="utf-8")
    if trusted_source is not None:
        module = _import_script(skill=skill, script_path=trusted_source)
        inspection = inspect_module(
            module,
            skill=skill,
            default_tool_id=resolved.stem,
            source=source,
            trusted=True,
        )
        input_model = module.Input
        output_model = module.Output
        run = module.run
    else:
        inspection = _inspect_script(
            resolved,
            skill=skill,
            expected_sha256=script_sha256,
            expected_tree_sha256=tree_sha256,
            reject_bytecode=reject_bytecode,
        )
        input_model = _IsolatedInput
        output_model = _IsolatedOutput
        run = None

    tool_id = validate_skill_token(inspection.tool_id, kind="tool")
    return SkillTool(
        skill=skill,
        tool_id=tool_id,
        description=inspection.description,
        scope=scope,
        requires_approval=inspection.requires_approval,
        deterministic=inspection.deterministic,
        effect=inspection.effect,
        idempotency=inspection.idempotency,
        allow_in_prod=inspection.allow_in_prod,
        timeout_seconds=inspection.timeout_seconds,
        secret_names=inspection.secret_names,
        network_hosts=inspection.network_hosts,
        filesystem_roots=inspection.filesystem_roots,
        script_path=resolved,
        script_sha256=script_sha256,
        skill_tree_sha256=tree_sha256,
        reject_bytecode=reject_bytecode,
        skills_root=skills_root.resolve(),
        input_model=input_model,
        output_model=output_model,
        input_schema=inspection.input_schema,
        output_schema=inspection.output_schema,
        run=run,
    )


def _trusted_source(script_path: Path) -> Path | None:
    return script_path if script_path.is_relative_to(default_skills_root().resolve()) else None


def _inspect_script(
    script_path: Path,
    *,
    skill: str,
    expected_sha256: str,
    expected_tree_sha256: str,
    reject_bytecode: bool,
) -> SkillInspection:
    try:
        with tempfile.TemporaryDirectory(prefix="roi-h-inspection-") as temporary:
            temporary_path = Path(temporary).resolve()
            request = json.dumps(
                {
                    "operation": "inspect",
                    "script": str(script_path),
                    "skill_root": str(script_path.parent.parent),
                    "expected_sha256": expected_sha256,
                    "expected_tree_sha256": expected_tree_sha256,
                    "reject_bytecode": reject_bytecode,
                    "temporary": str(temporary_path),
                    "skill": skill,
                }
            )
            completed = subprocess.run(  # noqa: S603 - fixed interpreter and sandbox
                _inspection_command(temporary_path),
                input=request,
                text=True,
                capture_output=True,
                check=False,
                cwd=Path(sys.executable).resolve().parent,
                env=isolated_process_environment(),
                timeout=_INSPECTION_TIMEOUT_SECONDS,
            )
    except subprocess.TimeoutExpired as exc:
        msg = f"skill inspection exceeded {_INSPECTION_TIMEOUT_SECONDS:g}s: {script_path}"
        raise TimeoutError(msg) from exc
    try:
        response = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        detail = (completed.stderr or completed.stdout)[-1000:]
        msg = f"skill inspection returned invalid JSON: {script_path}: {detail}"
        raise RuntimeError(msg) from exc
    if not isinstance(response, dict) or response.get("ok") is not True:
        error = response.get("error") if isinstance(response, dict) else "invalid response"
        msg = f"skill inspection failed: {script_path}: {error}"
        raise RuntimeError(msg)
    metadata = response.get("metadata")
    if not isinstance(metadata, dict):
        msg = f"skill inspection returned no metadata: {script_path}"
        raise TypeError(msg)
    return SkillInspection.model_validate_json(json.dumps(metadata))


def _inspection_command(temporary: Path) -> list[str]:
    worker = [sys.executable, "-m", "roi_h.harness.worker"]
    sandbox = shutil.which("sandbox-exec") if sys.platform == "darwin" else None
    if sandbox is None:
        return worker
    profile = (
        "(version 1) (allow default) (deny network*) "
        f"(deny file-write* (require-not (subpath {json.dumps(str(temporary))})))"
    )
    return [sandbox, "-p", profile, *worker]


def _import_script(*, skill: str, script_path: Path) -> ModuleType:
    resolved = script_path.resolve()
    digest = hashlib.sha256(f"{resolved}:{resolved.stat().st_mtime_ns}".encode()).hexdigest()[:12]
    module_name = f"{_SCRIPT_GLOBALS_PREFIX}{skill}_{script_path.stem}_{digest}"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, resolved)
    if spec is None or spec.loader is None:
        msg = f"cannot import skill script {script_path}"
        raise ImportError(msg)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def validate_skill_token(value: str, *, kind: str) -> str:
    """Validate skill or tool id tokens used in paths and CLI."""
    if not value or any(ch not in _SKILL_NAME_RE_OK for ch in value):
        msg = (
            f"invalid {kind} {value!r}: use letters, digits, '_' or '-' "
            "(no spaces or path separators)"
        )
        raise ValueError(msg)
    if value.startswith(("-", ".")):
        msg = f"invalid {kind} {value!r}: cannot start with '-' or '.'"
        raise ValueError(msg)
    return value
