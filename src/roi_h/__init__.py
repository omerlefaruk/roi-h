"""ActiveGraph-based RPA automation core."""

from roi_h.harness import (
    AutomationRegistry,
    LogicalPath,
    PathResolver,
    ProjectArchive,
    RetentionPlanner,
    RunSession,
    RunStorage,
    StoreLifecycle,
    WorkspaceCatalog,
)

__version__ = "0.1.0"

__all__ = [
    "AutomationRegistry",
    "LogicalPath",
    "PathResolver",
    "ProjectArchive",
    "RetentionPlanner",
    "RunSession",
    "RunStorage",
    "StoreLifecycle",
    "WorkspaceCatalog",
    "__version__",
]
