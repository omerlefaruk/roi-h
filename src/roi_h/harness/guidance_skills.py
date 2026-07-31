"""Markdown-only guidance skill discovery for automation authors."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_SKILL_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,63}$")
_MAX_DOCUMENT_BYTES = 1_000_000


@dataclass(frozen=True)
class GuidanceSkill:
    """One validated Markdown-only guidance skill."""

    name: str
    description: str
    version: str
    scope: str
    documents: dict[str, str]

    def to_dict(self, *, include_documents: bool = False) -> dict[str, Any]:
        """Return portable skill metadata and optional Markdown content."""
        value: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "version": self.version,
            "scope": self.scope,
            "valid": True,
            "documents": sorted(self.documents),
        }
        if include_documents:
            value["content"] = self.documents
        return value


def default_guidance_root() -> Path:
    """Resolve packaged guidance first, then the source checkout."""
    packaged = Path(__file__).resolve().parents[1] / "_skills"
    if packaged.is_dir():
        return packaged
    return Path(__file__).resolve().parents[3] / "skills"


def load_guidance_skills(
    *,
    shared_root: str | Path | None = None,
    project_root: str | Path | None = None,
) -> dict[str, GuidanceSkill]:
    """Load merged core, user, and project guidance with project precedence."""
    merged: dict[str, GuidanceSkill] = {}
    roots = [
        (default_guidance_root(), "core"),
        (Path(shared_root).resolve(), "shared") if shared_root is not None else None,
        (Path(project_root).resolve(), "project") if project_root is not None else None,
    ]
    for item in roots:
        if item is None:
            continue
        root, scope = item
        if not root.is_dir():
            continue
        for candidate in sorted(
            path for path in root.iterdir() if path.is_dir() and not path.is_symlink()
        ):
            if (candidate / "SKILL.md").is_file():
                skill = load_guidance_skill(candidate, scope=scope)
                merged[skill.name] = skill
    return merged


def load_guidance_skill(root: str | Path, *, scope: str) -> GuidanceSkill:
    """Validate and load one guidance skill with Markdown-only references."""
    supplied = Path(root).expanduser()
    if supplied.is_symlink():
        msg = f"guidance skill directory is a symbolic link: {supplied.name}"
        raise ValueError(msg)
    path = supplied.resolve()
    primary = path / "SKILL.md"
    if not primary.is_file() or primary.is_symlink():
        msg = f"guidance skill has no regular SKILL.md: {path.name}"
        raise FileNotFoundError(msg)
    documents: dict[str, str] = {}
    for item in sorted(path.rglob("*")):
        if item.is_symlink():
            msg = f"guidance skill contains a symbolic link: {item.relative_to(path).as_posix()}"
            raise ValueError(msg)
        if item.is_dir():
            continue
        if not item.resolve().is_relative_to(path):
            msg = f"guidance skill document escapes its root: {item.name}"
            raise ValueError(msg)
        relative = item.relative_to(path).as_posix()
        if item.suffix.lower() != ".md":
            msg = f"guidance skill contains a non-Markdown file: {relative}"
            raise ValueError(msg)
        if item.stat().st_size > _MAX_DOCUMENT_BYTES:
            msg = f"guidance skill document is too large: {relative}"
            raise ValueError(msg)
        documents[relative] = item.read_text(encoding="utf-8")
    metadata = _frontmatter(documents["SKILL.md"])
    name = metadata.get("name", "")
    if not _SKILL_NAME.fullmatch(name) or name != path.name:
        msg = f"guidance skill name must match its directory: {path.name!r}"
        raise ValueError(msg)
    description = metadata.get("description", "").strip()
    if not description:
        msg = f"guidance skill description is required: {name}"
        raise ValueError(msg)
    return GuidanceSkill(
        name=name,
        description=description,
        version=metadata.get("version", "").strip(),
        scope=scope,
        documents=documents,
    )


def _frontmatter(content: str) -> dict[str, str]:
    lines = content.splitlines()
    if len(lines) < 3 or lines[0].strip() != "---":
        msg = "SKILL.md must start with YAML-style frontmatter"
        raise ValueError(msg)
    metadata: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            return metadata
        key, separator, value = line.partition(":")
        if not separator or not key.strip():
            msg = f"invalid SKILL.md frontmatter line: {line!r}"
            raise ValueError(msg)
        metadata[key.strip()] = value.strip()
    msg = "SKILL.md frontmatter is not closed"
    raise ValueError(msg)


__all__ = [
    "GuidanceSkill",
    "default_guidance_root",
    "load_guidance_skill",
    "load_guidance_skills",
]
