"""Application seam: skills catalog + durable ActiveGraph runtime.

``RunSession`` is the small application seam over deeper modules:
phase machine, invoke ops, graph access, distill/export.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from activegraph import Graph, Object, Runtime

from roi_h.harness import graph_access, invoke_ops, phase_machine
from roi_h.harness import reconcile as reconcile_ops
from roi_h.harness.adaptive import build_adaptive_behavior, run_adaptive
from roi_h.harness.codex_provider import CodexCLIProvider
from roi_h.harness.control import request_cancellation
from roi_h.harness.custom import promote_advice
from roi_h.harness.domain import (
    BudgetSpec,
    InvocationIdentity,
    PhasePlanEntry,
    PhaseStatus,
    ReconciliationReport,
    StepResult,
    ToolInfo,
    parse_phase_plan,
)
from roi_h.harness.invocation_runtime import (
    IsolatedSkillInvoker,
    build_invocation_behaviors,
    recover_incomplete_invocations,
)
from roi_h.harness.loader import (
    SkillCatalog,
    load_skills,
    resolve_skills_root,
)
from roi_h.harness.records import RunRecord
from roi_h.harness.run_storage import RunStorage
from roi_h.harness.workspace import Workspace


@dataclass(frozen=True)
class RunSession:
    """Skills-based RPA harness with env-aware durability.

    Packaged core, user-shared, and project-local skills form the catalog.
    ActiveGraph owns the event log; ``runtime`` stays public.
    """

    runtime: Runtime
    catalog: SkillCatalog
    workspace: Workspace
    budget: BudgetSpec
    auto_approve: bool

    @property
    def database(self) -> Path:
        """SQLite path for this workspace environment."""
        return self.workspace.db

    @classmethod
    def create(
        cls,
        workspace: Workspace,
        *,
        run_id: str | None = None,
        skills_root: str | Path | None = None,
        project_skills: str | Path | None = None,
        budget: BudgetSpec | None = None,
        auto_approve: bool | None = None,
    ) -> RunSession:
        """Create a SQLite-backed run with tools bound from global + project skills.

        ``project_skills`` overrides the workspace project skills root (used when
        running a frozen automation package so the skill snapshot is authoritative).
        """
        spec = budget or BudgetSpec()
        project_root = (
            Path(project_skills) if project_skills is not None else workspace.project_skills
        )
        catalog = load_skills(
            resolve_skills_root(skills_root),
            shared_root=workspace.shared_skills if project_skills is None else None,
            project_root=project_root,
            database=workspace.db,
        )
        tools = catalog.to_activegraph_tools()
        limits = spec.to_activegraph_limits() or None
        graph = Graph(run_id=run_id)
        RunStorage(workspace).prepare(graph.run_id)
        invoker = IsolatedSkillInvoker(catalog, workspace)
        behaviors = (
            *build_invocation_behaviors(catalog, workspace, invoker),
            build_adaptive_behavior(),
        )
        runtime = Runtime(
            graph,
            persist_to=str(workspace.db),
            behaviors=behaviors,
            tools=tools,
            budget=limits,
            llm_provider=CodexCLIProvider(workspace.project_root),
            native_structured_output=True,
        )
        runtime.run_until_idle()
        return cls(
            runtime=runtime,
            catalog=catalog,
            workspace=workspace,
            budget=spec,
            auto_approve=_default_auto_approve(workspace.env, auto_approve),
        )

    @classmethod
    def reopen(
        cls,
        workspace: Workspace,
        *,
        run_id: str,
        skills_root: str | Path | None = None,
        project_skills: str | Path | None = None,
        budget: BudgetSpec | None = None,
        auto_approve: bool | None = None,
    ) -> RunSession:
        """Reopen a durable run and re-bind global + project skill tools."""
        spec = budget or BudgetSpec()
        RunStorage(workspace).prepare(run_id)
        project_root = (
            Path(project_skills) if project_skills is not None else workspace.project_skills
        )
        catalog = load_skills(
            resolve_skills_root(skills_root),
            shared_root=workspace.shared_skills if project_skills is None else None,
            project_root=project_root,
            database=workspace.db,
        )
        tools = catalog.to_activegraph_tools()
        limits = spec.to_activegraph_limits() or None
        invoker = IsolatedSkillInvoker(catalog, workspace)
        behaviors = (
            *build_invocation_behaviors(catalog, workspace, invoker),
            build_adaptive_behavior(),
        )
        runtime = Runtime.load(
            str(workspace.db),
            run_id=run_id,
            behaviors=behaviors,
            tools=tools,
            budget=limits,
            llm_provider=CodexCLIProvider(workspace.project_root),
            native_structured_output=True,
        )
        recover_incomplete_invocations(runtime)
        runtime.run_until_idle()
        return cls(
            runtime=runtime,
            catalog=catalog,
            workspace=workspace,
            budget=spec,
            auto_approve=_default_auto_approve(workspace.env, auto_approve),
        )

    def list_tools(self) -> list[ToolInfo]:
        """Return the AI-facing tool catalog derived from skills scripts."""
        return self.catalog.list_tools()

    def adapt(
        self,
        goal: str,
        *,
        tools: Sequence[str],
        max_turns: int = 6,
        actor: str = "codex",
    ) -> dict[str, Any]:
        """Run a bounded Codex-guided development loop through durable invocations."""
        return run_adaptive(
            self,
            goal,
            tools=tools,
            max_turns=max_turns,
            actor=actor,
        )

    def start_run(
        self,
        goal: str,
        *,
        actor: str = "ai",
        phase_plan: Sequence[str | PhasePlanEntry | dict[str, Any]] | None = None,
        automation_name: str | None = None,
        automation_version: str | None = None,
        package_digest: str | None = None,
    ) -> Object:
        """Materialize an ``rpa.run`` object for this harness session."""
        if not goal.strip():
            msg = "goal must be non-empty"
            raise ValueError(msg)
        plan = [entry.model_dump(mode="json") for entry in parse_phase_plan(phase_plan)]
        record = RunRecord(
            goal=goal,
            status="open",
            actor=actor,
            env=self.workspace.env,
            phase_plan=plan,
            automation_name=automation_name,
            automation_version=automation_version,
            package_digest=package_digest,
        )
        return self.runtime.graph.add_object("rpa.run", record.to_graph())

    def invoke(
        self,
        skill: str,
        tool: str,
        args: dict[str, Any] | None = None,
        *,
        actor: str = "ai",
        force: bool = False,
        identity: InvocationIdentity | None = None,
    ) -> StepResult:
        """Run ``skill.tool`` (or queue approval) and record an ``rpa.step``."""
        return invoke_ops.invoke(
            self.runtime,
            self.catalog,
            self.workspace,
            auto_approve=self.auto_approve,
            skill=skill,
            tool=tool,
            args=args,
            actor=actor,
            force=force,
            identity=identity,
        )

    def approve(
        self,
        approval_id: str,
        *,
        approved_by: str = "user",
    ) -> StepResult:
        """Execute a previously queued tool invocation."""
        return invoke_ops.approve(
            self.runtime,
            self.catalog,
            self.workspace,
            approval_id,
            approved_by=approved_by,
        )

    def list_approvals(self, *, status: str = "pending") -> list[dict[str, Any]]:
        """List approval objects, optionally filtered by status."""
        return invoke_ops.list_approvals(self.runtime, status=status)

    def cancel(self, *, reason: str = "operator requested cancellation") -> dict[str, Any]:
        """Request cooperative cancellation before the next recipe step."""
        run = graph_access.run_object(self.runtime)
        if run is None:
            msg = "rpa.run object missing; call start_run first"
            raise RuntimeError(msg)
        current = str(run.data.get("status") or "open")
        if current in {"completed", "failed", "cancelled"}:
            return {"ok": False, "run_id": self.runtime.run_id, "status": current}
        graph_access.patch_run(
            self.runtime,
            {
                "status": "cancel_requested",
                "cancel_reason": reason,
            },
        )
        request_cancellation(
            self.workspace,
            self.runtime.run_id,
            reason=reason,
        )
        return {
            "ok": True,
            "run_id": self.runtime.run_id,
            "status": "cancel_requested",
            "reason": reason,
        }

    def put_artifact(self, source: str | Path, *, name: str | None = None) -> dict[str, Any]:
        """Store a file artifact for this run and record an ``rpa.artifact`` object."""
        return phase_machine.put_artifact(self.runtime, self.workspace, source, name=name)

    def list_artifacts(self) -> list[dict[str, Any]]:
        """List file artifacts stored for this run."""
        return phase_machine.list_artifacts(self.workspace, self.runtime.run_id)

    def reconcile(self, *, repair: bool = False) -> ReconciliationReport:
        """Compare graph and filesystem state; optionally repair safe drift."""
        return reconcile_ops.reconcile_run(self.runtime, self.workspace, repair=repair)

    def begin_phase(
        self,
        name: str,
        *,
        description: str = "",
        require_artifacts: list[str] | None = None,
    ) -> dict[str, Any]:
        """Open a new phase; only one phase may be open at a time."""
        return phase_machine.begin_phase(
            self.runtime,
            name,
            description=description,
            require_artifacts=require_artifacts,
        )

    def end_phase(
        self,
        *,
        summary: dict[str, Any] | None = None,
        require_artifacts: list[str] | None = None,
        status: PhaseStatus = "done",
        error: str | None = None,
    ) -> dict[str, Any]:
        """Close the open phase, enforce artifact contract, write handoff package."""
        return phase_machine.end_phase(
            self.runtime,
            self.workspace,
            summary=summary,
            require_artifacts=require_artifacts,
            status=status,
            error=error,
        )

    def fail_phase(self, *, error: str, summary: dict[str, Any] | None = None) -> dict[str, Any]:
        """Mark the open phase as failed and write a partial handoff if artifacts exist."""
        return phase_machine.fail_phase(self.runtime, self.workspace, error=error, summary=summary)

    def skip_phase(self, name: str, *, reason: str = "") -> dict[str, Any]:
        """Record a skipped phase (no open phase required; closes open if same name)."""
        return phase_machine.skip_phase(self.runtime, self.workspace, name, reason=reason)

    def retry_phase(
        self,
        name: str,
        *,
        description: str = "",
        require_artifacts: list[str] | None = None,
    ) -> dict[str, Any]:
        """Begin a new open phase with the same name (prior instances stay in history)."""
        return phase_machine.retry_phase(
            self.runtime,
            name,
            description=description,
            require_artifacts=require_artifacts,
        )

    def list_phases(self) -> list[dict[str, Any]]:
        """List phases for this run with step counts."""
        return phase_machine.list_phases(self.runtime)

    def seed_from_handoff(self, handoff_path: str | Path) -> dict[str, Any]:
        """Inject completed phase(s) + artifacts from a handoff package or phases dir."""
        return phase_machine.seed_from_handoff(self.runtime, self.workspace, handoff_path)

    def status(self) -> dict[str, Any]:
        """Summarize this run for operators and external AIs."""
        runs = [
            {"id": item.id, "data": dict(item.data)}
            for item in self.runtime.graph.objects(type="rpa.run")
        ]
        steps = [
            {"id": item.id, "data": dict(item.data)}
            for item in self.runtime.graph.objects(type="rpa.step")
        ]
        phases = self.list_phases()
        run_data = runs[0]["data"] if runs else {}
        approvals = self.list_approvals(status="all")
        invocations = [
            {"id": item.id, "data": dict(item.data)}
            for item in self.runtime.graph.objects(type="rpa.invocation")
        ]
        adaptive_sessions = [
            {"id": item.id, "data": dict(item.data)}
            for item in self.runtime.graph.objects(type="rpa.adaptive.session")
        ]
        runtime_status = self.runtime.status()
        advice = promote_advice(self.catalog, steps)
        budget_snap = None
        if hasattr(runtime_status, "budget"):
            budget_snap = getattr(runtime_status.budget, "__dict__", None) or str(
                runtime_status.budget
            )
            if hasattr(runtime_status.budget, "snapshot"):
                budget_snap = runtime_status.budget.snapshot()
        pending = [item for item in approvals if item["data"].get("status") == "pending"]
        next_approve = None
        if pending:
            aid = pending[0].get("id") or pending[0]["data"].get("approval_id")
            next_approve = {
                "approval_id": aid,
                "command": (f"roi-h rpa approve --run-id {self.runtime.run_id} {aid} --by human"),
                "or_force": "roi-h rpa invoke … --force   /   start --auto-approve",
            }
        return {
            "run_id": self.runtime.run_id,
            "project": self.workspace.project,
            "project_root": str(self.workspace.project_root),
            "env": self.workspace.env,
            "db": str(self.workspace.db),
            "skills_root": str(self.catalog.global_root),
            "project_skills_root": str(self.workspace.project_skills),
            "artifacts_root": str(self.workspace.runs / self.runtime.run_id / "artifacts"),
            "phases_root": str(self.workspace.runs / self.runtime.run_id / "phases"),
            "auto_approve": self.auto_approve,
            "budget": self.budget.model_dump(mode="json"),
            "runtime_budget": budget_snap,
            "tool_count": len(self.catalog.tools),
            "project_tool_count": len(self.catalog.project_tools()),
            "runs": runs,
            "run_status": run_data.get("status"),
            "cancel_reason": run_data.get("cancel_reason"),
            "completed_at": run_data.get("completed_at"),
            "steps": steps,
            "step_count": len(steps),
            "ok_steps": sum(1 for step in steps if step["data"].get("status") == "ok"),
            "error_steps": sum(1 for step in steps if step["data"].get("status") == "error"),
            "pending_approvals": len(pending),
            "invocations": invocations,
            "invocation_count": len(invocations),
            "adaptive_sessions": adaptive_sessions,
            "adaptive_session_count": len(adaptive_sessions),
            "outcome_unknown_invocations": sum(
                1 for item in invocations if item["data"].get("status") == "outcome_unknown"
            ),
            "approvals": approvals,
            "next_approve": next_approve,
            "artifacts": self.list_artifacts(),
            "phases": phases,
            "phase_count": len(phases),
            "current_phase": run_data.get("current_phase"),
            "current_phase_id": run_data.get("current_phase_id"),
            "phase_plan": run_data.get("phase_plan") or [],
            "seeded_from": run_data.get("seeded_from"),
            "promote_advice": advice,
            "runtime": {
                "state": getattr(runtime_status, "state", None),
                "events_processed": getattr(runtime_status, "events_processed", None),
                "queue_depth": getattr(runtime_status, "queue_depth", None),
            },
        }


def _default_auto_approve(env: str, explicit: bool | None) -> bool:
    if explicit is not None:
        return explicit
    return env == "prod"
