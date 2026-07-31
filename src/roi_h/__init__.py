"""ActiveGraph-based RPA automation core."""

import tomllib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from roi_h.harness import (
    AutomationRegistry,
    LogicalPath,
    PathResolver,
    ProjectArchive,
    RetentionPlanner,
    RunStorage,
    StoreLifecycle,
    WorkspaceCatalog,
)


def _source_checkout_version() -> str:
    """Read the project version when distribution metadata is not installed."""
    for parent in Path(__file__).resolve().parents:
        pyproject = parent / "pyproject.toml"
        if not pyproject.is_file():
            continue
        with pyproject.open("rb") as stream:
            project = tomllib.load(stream).get("project", {})
        if project.get("name") == "roi-h" and isinstance(project.get("version"), str):
            return str(project["version"])
    return "0+unknown"


try:
    __version__ = version("roi-h")
except PackageNotFoundError:
    __version__ = _source_checkout_version()

__all__ = [
    "AutomationRegistry",
    "LogicalPath",
    "PathResolver",
    "ProjectArchive",
    "RetentionPlanner",
    "RunStorage",
    "StoreLifecycle",
    "WorkspaceCatalog",
    "__version__",
]
