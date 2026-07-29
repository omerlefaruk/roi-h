"""Stable public interfaces for the ActiveGraph RPA core."""

from roi_h.harness.application import RunSession
from roi_h.harness.interfaces import AutomationRegistry, WorkspaceCatalog
from roi_h.harness.logical_paths import LogicalPath, PathResolver
from roi_h.harness.project_archive import ProjectArchive
from roi_h.harness.retention import RetentionPlanner
from roi_h.harness.run_storage import RunStorage
from roi_h.harness.store_lifecycle import StoreLifecycle

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
]
