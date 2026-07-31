"""Small, stable entry points over the harness implementation modules."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from roi_h.harness.automation import list_automations, load_automation
from roi_h.harness.automation_source import put_source
from roi_h.harness.journeys import run_automation, run_development_source, ship_automation
from roi_h.harness.workspace import Workspace, create_project, list_projects


@dataclass(frozen=True)
class WorkspaceCatalog:
    """Own project discovery and environment selection for one ROI-H home."""

    root: Path

    @classmethod
    def at(cls, root: str | Path) -> WorkspaceCatalog:
        return cls(Path(root).expanduser().resolve())

    def create(
        self,
        name: str,
        *,
        display_name: str = "",
        env: str = "dev",
    ) -> Workspace:
        create_project(self.root, name, display_name=display_name, env=env)
        return self.open(name, env=env)

    def open(self, project: str, *, env: str | None = None) -> Workspace:
        return Workspace.open(self.root, project=project, env=env)

    def list(self) -> list[dict[str, Any]]:
        return list_projects(self.root)


@dataclass(frozen=True)
class AutomationRegistry:
    """Create, verify, ship, and execute modular automations."""

    workspace: Workspace

    def list(self) -> list[dict[str, Any]]:
        return list_automations(self.workspace)

    def load(self, name: str, *, version: str | None = None) -> dict[str, Any]:
        return load_automation(self.workspace, name, version=version)

    def put_source(
        self,
        name: str,
        *,
        manifest: dict[str, Any],
        files: dict[str, str],
    ) -> dict[str, Any]:
        snapshot = put_source(self.workspace.automation_sources, name, manifest, files)
        return snapshot.model_dump(mode="json")

    def run_development(
        self,
        name: str,
        *,
        run_id: str | None = None,
        goal: str = "",
        inputs: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return run_development_source(
            self.workspace,
            name=name,
            run_id=run_id,
            goal=goal,
            inputs=inputs,
        )

    def ship(
        self,
        *,
        name: str,
        version: str,
        from_run: str,
        goal: str = "",
        notes: str = "",
    ) -> dict[str, Any]:
        return ship_automation(
            self.workspace,
            name=name,
            version=version,
            from_run=from_run,
            goal=goal,
            notes=notes,
        )

    def run(
        self,
        name: str,
        *,
        version: str | None = None,
        run_id: str | None = None,
        inputs: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return run_automation(
            self.workspace,
            name=name,
            version=version,
            run_id=run_id,
            inputs=inputs,
        )


__all__ = ["AutomationRegistry", "WorkspaceCatalog"]
