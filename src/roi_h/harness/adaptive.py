"""Bounded adaptive execution driven by an ActiveGraph LLM behavior."""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from typing import Any, Literal

from activegraph import Event, LLMBehavior, Runtime
from pydantic import BaseModel, ConfigDict, Field, model_validator

from roi_h.harness.control import cancellation_request
from roi_h.harness.domain import StepResult
from roi_h.harness.loader import SkillCatalog, SkillTool

_BEHAVIOR_NAME = "roi_h.adaptive_decision"
_TURN_EVENT = "rpa.adaptive.turn"


class AdaptiveDecision(BaseModel):
    """One typed Codex decision in the bounded adaptive loop."""

    model_config = ConfigDict(extra="forbid")

    action: Literal["invoke", "finish"]
    tool: str | None
    args_json: str = Field(
        description="Tool arguments encoded as a JSON object string.",
    )
    summary: str

    @model_validator(mode="after")
    def validate_action(self) -> AdaptiveDecision:
        if self.action == "invoke" and (not self.tool or "." not in self.tool):
            msg = "invoke decisions require a canonical skill.tool name"
            raise ValueError(msg)
        try:
            args = json.loads(self.args_json)
        except json.JSONDecodeError as exc:
            msg = "args_json must encode a JSON object"
            raise ValueError(msg) from exc
        if not isinstance(args, dict):
            msg = "args_json must encode a JSON object"
            raise TypeError(msg)
        return self

    def arguments(self) -> dict[str, Any]:
        """Return the validated tool-argument object."""
        return dict(json.loads(self.args_json))


def build_adaptive_behavior() -> LLMBehavior:
    """Create the single Codex-backed decision behavior."""

    def placeholder(_event: Event, _graph: Any, _ctx: Any) -> None:
        return None

    def handle(event: Event, graph: Any, _ctx: Any, output: AdaptiveDecision) -> None:
        decision = output.model_dump(mode="json", exclude={"args_json"})
        graph.add_object(
            "rpa.adaptive.decision",
            {
                "session_id": str(event.payload["session_id"]),
                "turn": int(event.payload["turn"]),
                "turn_event_id": event.id,
                **decision,
                "args": output.arguments(),
            },
        )

    return LLMBehavior(
        name=_BEHAVIOR_NAME,
        fn=placeholder,
        handler=handle,
        on=[_TURN_EVENT],
        description=(
            "Choose exactly one next action for a bounded ROI-H development run. "
            "For action=invoke, select only a tool included in the triggering "
            "event's tools list and encode its arguments as a JSON object string in "
            "args_json. Use action=finish when the goal is satisfied. Never invent "
            "tool names."
        ),
        output_schema=AdaptiveDecision,
        deterministic=True,
        temperature=0.0,
        max_tool_turns=1,
    )


def run_adaptive(
    session: Any,
    goal: str,
    *,
    tools: Sequence[str],
    max_turns: int = 6,
    actor: str = "codex",
) -> dict[str, Any]:
    """Let Codex choose bounded steps while ROI-H retains tool authority."""
    if session.workspace.env != "dev":
        msg = "adaptive execution is restricted to dev"
        raise ValueError(msg)
    if not session.auto_approve:
        msg = "adaptive execution requires auto_approve=True"
        raise ValueError(msg)
    if not goal.strip():
        msg = "adaptive goal must be non-empty"
        raise ValueError(msg)
    if max_turns < 1:
        msg = "max_turns must be at least 1"
        raise ValueError(msg)

    allowed = _resolve_allowed_tools(session.catalog, tools)
    run_objects = session.runtime.graph.objects(type="rpa.run")
    if not run_objects:
        msg = "rpa.run object missing; call start_run first"
        raise RuntimeError(msg)

    session_id = f"adaptive_{uuid.uuid4().hex[:20]}"
    session_object = session.runtime.graph.add_object(
        "rpa.adaptive.session",
        {
            "session_id": session_id,
            "goal": goal,
            "status": "running",
            "allowed_tools": [tool.name for tool in allowed],
            "max_turns": max_turns,
        },
    )
    executed: list[dict[str, Any]] = []
    last_result: dict[str, Any] | None = None
    tool_defs = [_tool_definition(tool) for tool in allowed]

    for turn in range(1, max_turns + 1):
        cancellation = cancellation_request(session.workspace, session.runtime.run_id)
        if cancellation is not None:
            result = {
                "ok": False,
                "status": "cancelled",
                "session_id": session_id,
                "turns": turn - 1,
                "executed": executed,
                "error": str(cancellation.get("reason") or "run cancelled"),
            }
            session.runtime.graph.patch_object(session_object.id, result)
            return result
        event = _emit_turn(
            session.runtime,
            {
                "session_id": session_id,
                "turn": turn,
                "goal": goal,
                "tools": tool_defs,
                "last_result": last_result,
            },
            actor=actor,
        )
        session.runtime.run_until_idle()
        decision = _decision_for_event(session.runtime, event.id)
        if decision is None:
            failure = _behavior_failure(session.runtime, event.id)
            result = {
                "ok": False,
                "status": "failed",
                "session_id": session_id,
                "turns": turn,
                "executed": executed,
                "error": failure or "Codex produced no adaptive decision",
            }
            session.runtime.graph.patch_object(session_object.id, result)
            return result

        data = dict(decision.data)
        if data["action"] == "finish":
            result = {
                "ok": True,
                "status": "completed",
                "session_id": session_id,
                "turns": turn,
                "summary": str(data.get("summary") or ""),
                "executed": executed,
            }
            session.runtime.graph.patch_object(session_object.id, result)
            return result

        name = str(data["tool"])
        if name not in {tool.name for tool in allowed}:
            last_result = {
                "status": "error",
                "error": f"Codex selected disallowed tool {name!r}",
            }
            continue
        skill, _, tool = name.partition(".")
        step = session.invoke(
            skill,
            tool,
            dict(data.get("args") or {}),
            actor=actor,
        )
        last_result = _step_payload(step)
        executed.append(last_result)

    result = {
        "ok": False,
        "status": "max_turns",
        "session_id": session_id,
        "turns": max_turns,
        "executed": executed,
        "error": f"adaptive execution reached max_turns={max_turns}",
    }
    session.runtime.graph.patch_object(session_object.id, result)
    return result


def _resolve_allowed_tools(catalog: SkillCatalog, names: Sequence[str]) -> list[SkillTool]:
    if not names:
        msg = "adaptive execution requires at least one explicit tool"
        raise ValueError(msg)
    resolved: list[SkillTool] = []
    seen: set[str] = set()
    for name in names:
        skill, separator, tool = name.partition(".")
        if not separator:
            msg = f"adaptive tool must use skill.tool naming: {name!r}"
            raise ValueError(msg)
        item = catalog.resolve(skill, tool)
        if item.effect == "destructive":
            msg = f"adaptive execution forbids destructive tool {item.name!r}"
            raise ValueError(msg)
        if item.name not in seen:
            seen.add(item.name)
            resolved.append(item)
    return resolved


def _emit_turn(runtime: Runtime, payload: dict[str, Any], *, actor: str) -> Event:
    event = Event(
        id=runtime.graph.ids.event(),
        type=_TURN_EVENT,
        payload=payload,
        actor=actor,
        frame_id=runtime.frame.id if runtime.frame else None,
        timestamp=runtime.graph.clock.now(),
    )
    runtime.graph.emit(event)
    return event


def _tool_definition(tool: SkillTool) -> dict[str, Any]:
    return {
        "name": tool.name,
        "description": tool.description,
        "effect": tool.effect,
        "input_schema": tool.input_model.model_json_schema(),
    }


def _decision_for_event(runtime: Runtime, event_id: str) -> Any | None:
    return next(
        (
            item
            for item in reversed(runtime.graph.objects(type="rpa.adaptive.decision"))
            if item.data.get("turn_event_id") == event_id
        ),
        None,
    )


def _behavior_failure(runtime: Runtime, event_id: str) -> str | None:
    failure = next(
        (
            item
            for item in reversed(runtime.errors)
            if item.behavior == _BEHAVIOR_NAME and item.event_id == event_id
        ),
        None,
    )
    return failure.message if failure is not None else None


def _step_payload(step: StepResult) -> dict[str, Any]:
    return {
        "step_id": step.step_id,
        "skill": step.skill,
        "tool": step.tool,
        "status": step.status,
        "output": step.output,
        "error": step.error,
        "failure": step.failure.model_dump(mode="json") if step.failure else None,
    }


__all__ = ["AdaptiveDecision", "build_adaptive_behavior", "run_adaptive"]
