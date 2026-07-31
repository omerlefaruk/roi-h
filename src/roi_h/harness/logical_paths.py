"""Portable logical paths and the single physical-path resolution seam."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal, cast

from roi_h.harness.workspace import Workspace

PathIntent = Literal["read", "create", "replace", "delete"]
LogicalScheme = Literal["project", "run", "artifact", "automation"]
_SCHEMES = frozenset({"project", "run", "artifact", "automation"})
_RUN_ROOTS = frozenset({"input", "work", "output", "tmp"})
_WINDOWS_RESERVED = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{i}" for i in range(1, 10)),
        *(f"lpt{i}" for i in range(1, 10)),
    }
)
_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:")
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ARTIFACT_ID = re.compile(r"^art_[A-Za-z0-9_-]{8,128}$")
_AUTOMATION_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_PATH_FIELD = re.compile(
    r"(?:^|_)(?:path|file|source|destination|directory|dir|output|input)(?:$|_)",
    re.IGNORECASE,
)


class LogicalPathError(ValueError):
    """A stable logical-path policy failure."""

    code = "path.invalid_logical_path"


class PathCapabilityError(PermissionError):
    """A tool requested a logical root it did not declare."""

    code = "path.capability_denied"


@dataclass(frozen=True)
class LogicalPath:
    """Strict portable path value stored in manifests and durable records."""

    scheme: LogicalScheme
    root: str
    segments: tuple[str, ...] = ()

    @classmethod
    def parse(cls, raw: str) -> LogicalPath:
        """Parse and validate one canonical ``scheme://`` path."""
        if not isinstance(raw, str) or "://" not in raw:
            msg = f"path.invalid_logical_path: expected logical path, got {raw!r}"
            raise LogicalPathError(msg)
        scheme_raw, remainder = raw.split("://", 1)
        if scheme_raw not in _SCHEMES:
            msg = f"path.invalid_logical_path: unsupported scheme {scheme_raw!r}"
            raise LogicalPathError(msg)
        if not remainder or remainder.startswith("/"):
            msg = "path.invalid_logical_path: logical root is missing"
            raise LogicalPathError(msg)
        pieces = tuple(remainder.split("/"))
        _validate_segments(pieces)
        scheme: LogicalScheme = scheme_raw  # type: ignore[assignment]
        root, segments = pieces[0], pieces[1:]
        if scheme == "project" and root != "reference":
            msg = "path.invalid_logical_path: project root must be reference"
            raise LogicalPathError(msg)
        if scheme == "run" and root not in _RUN_ROOTS:
            msg = f"path.invalid_logical_path: run root must be one of {sorted(_RUN_ROOTS)}"
            raise LogicalPathError(msg)
        if scheme == "artifact" and (segments or not _ARTIFACT_ID.fullmatch(root)):
            msg_0 = "path.invalid_logical_path: artifact URI must be artifact://<artifact-id>"
            raise LogicalPathError(msg_0)
        if scheme == "automation" and (not _AUTOMATION_NAME.fullmatch(root) or not segments):
            msg = "path.invalid_logical_path: automation URI requires name and version"
            raise LogicalPathError(msg)
        return cls(scheme=scheme, root=root, segments=segments)

    def __str__(self) -> str:
        """Return the canonical durable representation."""
        suffix = "/".join((self.root, *self.segments))
        return f"{self.scheme}://{suffix}"

    @property
    def capability(self) -> str:
        """Return the canonical filesystem capability family."""
        if self.scheme == "project":
            return "project:reference"
        if self.scheme == "run":
            return f"run:{self.root}"
        if self.scheme == "artifact":
            return "artifact"
        return "automation"


@dataclass(frozen=True)
class PathScope:
    """Already-resolved identity needed to resolve a logical path."""

    workspace: Workspace
    run_id: str | None = None
    automation_name: str | None = None
    automation_version: str | None = None


@dataclass(frozen=True)
class ResolvedPath:
    """Validated physical path plus its portable identity."""

    logical: LogicalPath
    physical: Path
    intent: PathIntent
    read_only: bool


class PathResolver:
    """Own layout joining, containment, symlink, and portability policy."""

    def resolve(
        self,
        logical_path: str | LogicalPath,
        scope: PathScope,
        intent: PathIntent = "read",
    ) -> ResolvedPath:
        logical = (
            logical_path
            if isinstance(logical_path, LogicalPath)
            else LogicalPath.parse(logical_path)
        )
        workspace = scope.workspace
        read_only = logical.scheme in {"project", "artifact", "automation"} or (
            logical.scheme == "run" and logical.root == "input"
        )
        brokered_input_create = (
            logical.scheme == "run" and logical.root == "input" and intent == "create"
        )
        if read_only and intent != "read" and not brokered_input_create:
            msg = f"path.capability_denied: {logical.scheme} paths are read-only"
            raise PathCapabilityError(msg)

        if logical.scheme == "project":
            root = workspace.reference
            boundary = workspace.project_root
            relative = logical.segments
        elif logical.scheme == "run":
            if not scope.run_id:
                msg = "path.invalid_logical_path: run scope is required"
                raise LogicalPathError(msg)
            run_id = validate_run_id(scope.run_id)
            root = workspace.runs / run_id / "workspace" / logical.root
            boundary = workspace.environment_root
            relative = logical.segments
        elif logical.scheme == "artifact":
            if not scope.run_id:
                msg = "path.invalid_logical_path: run scope is required"
                raise LogicalPathError(msg)
            run_id = validate_run_id(scope.run_id)
            boundary = workspace.environment_root
            artifact_root = _resolve_scoped_root(
                workspace.runs / run_id / "artifacts",
                boundary,
            )
            matches = list(artifact_root.glob(f"{logical.root}--*"))
            if len(matches) != 1:
                msg = f"artifact.file_missing: {logical.root}"
                raise FileNotFoundError(msg)
            root = matches[0]
            relative = ()
        else:
            version = logical.segments[0]
            root = workspace.automations / logical.root / version
            boundary = workspace.project_root
            relative = logical.segments[1:]

        root = _resolve_scoped_root(root, boundary)
        candidate = root.joinpath(*relative)
        _assert_contained(candidate, root)
        _assert_no_escaping_symlink(candidate, root)
        if intent in {"create", "replace"}:
            candidate.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        return ResolvedPath(
            logical=logical,
            physical=candidate,
            intent=intent,
            read_only=read_only,
        )

    def normalize(self, physical_path: str | Path, scope: PathScope) -> LogicalPath:
        """Convert a contained physical path to its portable logical identity."""
        path = Path(physical_path).expanduser()
        if not path.is_absolute():
            if not scope.run_id:
                msg = "path.invalid_logical_path: relative path needs run scope"
                raise LogicalPathError(msg)
            path = (
                scope.workspace.runs / validate_run_id(scope.run_id) / "workspace" / "work" / path
            )
        path = path.resolve()
        workspace = scope.workspace
        roots: list[tuple[Path, LogicalScheme, str]] = [
            (workspace.reference.resolve(), "project", "reference"),
        ]
        if scope.run_id:
            run = workspace.runs / validate_run_id(scope.run_id)
            roots.extend(
                [((run / "workspace" / name).resolve(), "run", name) for name in sorted(_RUN_ROOTS)]
            )
            artifact_root = (run / "artifacts").resolve()
            try:
                relative = path.relative_to(artifact_root)
            except ValueError:
                pass
            else:
                if not relative.parts:
                    msg = "path.invalid_logical_path: artifact root is not a file"
                    raise LogicalPathError(msg)
                artifact_id = relative.parts[0].split("--", 1)[0]
                return LogicalPath.parse(f"artifact://{artifact_id}")
        for root, scheme, logical_root in roots:
            try:
                relative = path.relative_to(root)
            except ValueError:
                continue
            return LogicalPath(
                scheme=scheme,
                root=logical_root,
                segments=tuple(relative.parts),
            )
        try:
            package_relative = path.relative_to(workspace.automations.resolve())
        except ValueError as exc:
            msg = "path.escape_denied: physical path is outside the selected scope"
            raise LogicalPathError(msg) from exc
        if len(package_relative.parts) < 2:
            msg = "path.invalid_logical_path: incomplete automation path"
            raise LogicalPathError(msg)
        return LogicalPath(
            scheme="automation",
            root=package_relative.parts[0],
            segments=tuple(package_relative.parts[1:]),
        )


def materialize_tool_payload(
    payload: dict[str, Any],
    *,
    scope: PathScope,
    capabilities: tuple[str, ...] | list[str],
    effect: str,
) -> dict[str, Any]:
    """Resolve path-bearing input fields immediately before worker execution."""
    resolver = PathResolver()
    allowed = frozenset(capabilities)

    def visit(value: Any, key: str | None = None) -> Any:
        if isinstance(value, dict):
            return {str(k): visit(v, str(k)) for k, v in value.items()}
        if isinstance(value, list):
            return [visit(item, key) for item in value]
        if isinstance(value, tuple):
            return [visit(item, key) for item in value]
        if not isinstance(value, str):
            return value
        is_logical = any(value.startswith(f"{scheme}://") for scheme in _SCHEMES)
        is_path_field = bool(key and _PATH_FIELD.search(key))
        if not is_logical and not is_path_field:
            return value
        if not is_logical:
            if _looks_absolute(value):
                msg = "path.invalid_logical_path: absolute physical paths are not accepted"
                raise LogicalPathError(msg)
            value = f"run://work/{PurePosixPath(value).as_posix()}"
        logical = LogicalPath.parse(value)
        required = _capability_for(logical)
        intent: PathIntent = "read" if effect == "read" or required.endswith(":read") else "create"
        if required not in allowed:
            msg = f"path.capability_denied: {required} is not declared; declared={sorted(allowed)}"
            raise PathCapabilityError(msg)
        return str(resolver.resolve(logical, scope, intent).physical)

    return cast("dict[str, Any]", visit(payload))


def normalize_tool_output(value: Any, *, scope: PathScope) -> Any:
    """Replace path-bearing worker output with logical paths before persistence."""
    resolver = PathResolver()

    def visit(item: Any, key: str | None = None) -> Any:
        if isinstance(item, dict):
            return {str(k): visit(v, str(k)) for k, v in item.items()}
        if isinstance(item, list):
            return [visit(child, key) for child in item]
        if not isinstance(item, str) or not key or not _PATH_FIELD.search(key):
            return item
        if "://" in item:
            return str(LogicalPath.parse(item))
        try:
            return str(resolver.normalize(item, scope))
        except LogicalPathError as exc:
            msg = f"path.escape_denied: tool returned path outside allowed roots in {key!r}"
            raise LogicalPathError(msg) from exc

    return visit(value)


def detect_physical_paths(value: Any) -> list[str]:
    """Find absolute path strings in a nested manifest or durable record."""
    found: list[str] = []

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for child in item.values():
                visit(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                visit(child)
        elif isinstance(item, str) and _looks_absolute(item):
            found.append(item)

    visit(value)
    return found


def validate_run_id(run_id: str) -> str:
    """Reject run identities that can escape the selected environment."""
    if not _RUN_ID.fullmatch(run_id):
        msg = f"path.invalid_logical_path: invalid run id {run_id!r}"
        raise LogicalPathError(msg)
    return run_id


def _capability_for(path: LogicalPath) -> str:
    suffix = "read-write"
    if path.scheme in {"project", "artifact", "automation"} or (
        path.scheme == "run" and path.root == "input"
    ):
        suffix = "read"
    return f"{path.capability}:{suffix}"


def _validate_segments(segments: tuple[str, ...]) -> None:
    if not segments or len("/".join(segments).encode("utf-8")) > 3_000:
        msg = "path.invalid_logical_path: path is empty or too long"
        raise LogicalPathError(msg)
    for segment in segments:
        normalized = unicodedata.normalize("NFC", segment)
        stem = segment.split(".", 1)[0].casefold()
        if (
            not segment
            or segment in {".", ".."}
            or "\x00" in segment
            or "\\" in segment
            or "/" in segment
            or normalized != segment
            or _DRIVE_PREFIX.match(segment)
            or stem in _WINDOWS_RESERVED
        ):
            msg = f"path.invalid_logical_path: unsafe segment {segment!r}"
            raise LogicalPathError(msg)


def _looks_absolute(value: str) -> bool:
    return value.startswith(("/", "\\\\")) or bool(_DRIVE_PREFIX.match(value))


def _resolve_scoped_root(root: Path, boundary: Path) -> Path:
    _assert_contained(root, boundary)
    _assert_no_symlink(root, boundary)
    resolved = root.resolve()
    _assert_contained(resolved, boundary.resolve())
    return resolved


def _assert_contained(candidate: Path, root: Path) -> None:
    try:
        candidate.absolute().relative_to(root.absolute())
    except ValueError as exc:
        msg = "path.escape_denied: path escapes selected root"
        raise LogicalPathError(msg) from exc


def _assert_no_symlink(candidate: Path, root: Path) -> None:
    current = root
    for segment in candidate.absolute().relative_to(root.absolute()).parts:
        current = current / segment
        if current.is_symlink():
            msg = "path.escape_denied: logical root contains a symlink"
            raise LogicalPathError(msg)


def _assert_no_escaping_symlink(candidate: Path, root: Path) -> None:
    current = root
    for segment in candidate.absolute().relative_to(root.absolute()).parts:
        current = current / segment
        if not current.is_symlink():
            continue
        try:
            current.resolve().relative_to(root.resolve())
        except ValueError as exc:
            msg = "path.escape_denied: symlink escapes selected root"
            raise LogicalPathError(msg) from exc


__all__ = [
    "LogicalPath",
    "LogicalPathError",
    "PathCapabilityError",
    "PathIntent",
    "PathResolver",
    "PathScope",
    "ResolvedPath",
    "detect_physical_paths",
    "materialize_tool_payload",
    "normalize_tool_output",
    "validate_run_id",
]
