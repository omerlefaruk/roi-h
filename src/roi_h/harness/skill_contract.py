"""Typed executable skill metadata shared by trusted and isolated inspection."""

from __future__ import annotations

import hashlib
import importlib.machinery
import re
from pathlib import Path
from types import ModuleType
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field

_READ_PREFIXES = (
    "find",
    "get",
    "list",
    "read",
    "hash",
    "snapshot",
    "status",
    "schema",
    "score",
    "verify",
    "extract",
)
_DESTRUCTIVE_TOKENS = ("delete", "remove", "drop", "purge", "shell", "run")
_REQUIRED_SECRET_RE = re.compile(r"""require_secret\(\s*["']([A-Za-z0-9_.-]+)["']""")


class SkillInspection(BaseModel):
    """Serializable executable contract returned by skill inspection."""

    model_config = ConfigDict(extra="forbid", strict=True)

    tool_id: str
    description: str
    deterministic: bool
    requires_approval: bool
    effect: Literal["read", "write", "destructive"]
    idempotency: Literal["none", "key", "reconcile"]
    allow_in_prod: bool
    timeout_seconds: float = Field(gt=0)
    secret_names: tuple[str, ...]
    network_hosts: tuple[str, ...]
    filesystem_roots: tuple[str, ...]
    input_schema: dict[str, object]
    output_schema: dict[str, object]


def inspect_module(
    module: ModuleType,
    *,
    skill: str,
    default_tool_id: str,
    source: str,
    trusted: bool,
) -> SkillInspection:
    """Extract one typed contract from an imported skill module."""
    input_model = getattr(module, "Input", None)
    output_model = getattr(module, "Output", None)
    run = getattr(module, "run", None)
    if not (
        isinstance(input_model, type)
        and issubclass(input_model, BaseModel)
        and isinstance(output_model, type)
        and issubclass(output_model, BaseModel)
        and callable(run)
    ):
        msg = "skill tool must define Input(BaseModel), Output(BaseModel), and run(args)"
        raise TypeError(msg)

    tool_id = getattr(module, "TOOL_ID", default_tool_id)
    effect = _effect(module, skill=skill, tool_id=tool_id, trusted=trusted)
    deterministic = getattr(module, "DETERMINISTIC", False)
    if not isinstance(deterministic, bool):
        msg = "DETERMINISTIC must be a boolean"
        raise TypeError(msg)
    if effect != "read":
        deterministic = False
    default_timeout = 120.0
    if trusted and skill == "shell":
        default_timeout = 3600.0
    elif trusted and skill == "browser":
        default_timeout = 180.0
    timeout = getattr(module, "TIMEOUT_SECONDS", default_timeout)
    declared_secrets = _string_tuple(module, "SECRET_NAMES")
    allow_in_prod_default = effect != "destructive" and skill != "shell" if trusted else False
    return SkillInspection(
        tool_id=tool_id,
        description=getattr(module, "DESCRIPTION", f"{skill}.{tool_id}"),
        deterministic=deterministic,
        requires_approval=getattr(module, "REQUIRES_APPROVAL", not trusted),
        effect=effect,
        idempotency=_idempotency(module, effect=effect),
        allow_in_prod=getattr(module, "ALLOW_IN_PROD", allow_in_prod_default),
        timeout_seconds=timeout,
        secret_names=tuple(sorted({*declared_secrets, *_REQUIRED_SECRET_RE.findall(source)})),
        network_hosts=_string_tuple(module, "NETWORK_HOSTS"),
        filesystem_roots=_string_tuple(module, "FILESYSTEM_ROOTS"),
        input_schema=strict_skill_schema(input_model),
        output_schema=strict_skill_schema(output_model),
    )


def skill_tree_digest(root: Path, *, reject_bytecode: bool) -> str:
    """Hash one skill tree and reject unsafe filesystem entries."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            msg = f"skill tree contains a symlink: {path}"
            raise ValueError(msg)
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        bytecode = path.suffix == ".pyc" or "__pycache__" in path.parts
        if reject_bytecode and bytecode:
            msg = f"custom skill tree contains Python bytecode: {relative}"
            raise ValueError(msg)
        if bytecode:
            continue
        if reject_bytecode and any(
            path.name.endswith(suffix) for suffix in importlib.machinery.EXTENSION_SUFFIXES
        ):
            msg = f"custom skill tree contains a native Python extension: {relative}"
            raise ValueError(msg)
        data = path.read_bytes()
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def strict_skill_model(model: type[BaseModel]) -> type[BaseModel]:
    """Return an inherited model with recursive strict, closed validation."""
    config = ConfigDict(**{**model.model_config, "extra": "forbid", "strict": True})

    def model_validate(cls: Any, value: object, /, *args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("strict", True)
        kwargs.setdefault("extra", "forbid")
        return cls.__pydantic_validator__.validate_python(value, *args, **kwargs)

    strict_model = type(
        f"Strict{model.__name__}",
        (model,),
        {"model_config": config, "model_validate": classmethod(model_validate)},
    )
    return cast("type[BaseModel]", strict_model)


def strict_skill_schema(model: type[BaseModel]) -> dict[str, object]:
    """Return the JSON Schema for recursive strict, closed model validation."""
    schema = strict_skill_model(model).model_json_schema()

    def close_objects(value: object) -> None:
        if isinstance(value, dict):
            if value.get("type") == "object" and "properties" in value:
                value.setdefault("additionalProperties", False)
            for item in value.values():
                close_objects(item)
        elif isinstance(value, list):
            for item in value:
                close_objects(item)

    close_objects(schema)
    return schema


def _effect(
    module: ModuleType,
    *,
    skill: str,
    tool_id: str,
    trusted: bool,
) -> Literal["read", "write", "destructive"]:
    declared = getattr(module, "TOOL_EFFECT", None)
    if declared in {"read", "write", "destructive"}:
        return cast("Literal['read', 'write', 'destructive']", declared)
    if declared is not None:
        msg = "TOOL_EFFECT must be 'read', 'write', or 'destructive'"
        raise TypeError(msg)
    if not trusted:
        return "destructive"
    lowered = f"{skill}.{tool_id}".lower()
    if skill == "shell" or any(token in lowered for token in _DESTRUCTIVE_TOKENS):
        return "destructive"
    if tool_id.lower().startswith(_READ_PREFIXES):
        return "read"
    return "write"


def _idempotency(
    module: ModuleType,
    *,
    effect: Literal["read", "write", "destructive"],
) -> Literal["none", "key", "reconcile"]:
    declared = getattr(module, "IDEMPOTENCY", None)
    if declared in {"none", "key", "reconcile"}:
        return cast("Literal['none', 'key', 'reconcile']", declared)
    if declared is not None:
        msg = "IDEMPOTENCY must be 'none', 'key', or 'reconcile'"
        raise TypeError(msg)
    return "none" if effect == "read" else "reconcile"


def _string_tuple(module: ModuleType, name: str) -> tuple[str, ...]:
    value = getattr(module, name, ())
    if not isinstance(value, (list, tuple)) or not all(isinstance(item, str) for item in value):
        msg = f"{name} must be a sequence of strings"
        raise TypeError(msg)
    return tuple(value)


__all__ = [
    "SkillInspection",
    "inspect_module",
    "skill_tree_digest",
    "strict_skill_model",
    "strict_skill_schema",
]
