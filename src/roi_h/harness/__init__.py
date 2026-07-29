"""Stable public interfaces for the ActiveGraph RPA core."""

from roi_h.harness.application import RunSession
from roi_h.harness.interfaces import AutomationRegistry, WorkspaceCatalog

__all__ = ["AutomationRegistry", "RunSession", "WorkspaceCatalog"]
