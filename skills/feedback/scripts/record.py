from __future__ import annotations
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from pydantic import BaseModel, Field
TOOL_ID="record"
DESCRIPTION="Record post-automation feedback for codebase/skill improvement."
DETERMINISTIC=True
REQUIRES_APPROVAL=False
class Input(BaseModel):
    automation: str = ""
    version: str = ""
    ok: bool = True
    summary: dict = Field(default_factory=dict)
    suggestions: list[str] = Field(default_factory=list)
    notes: str = ""
    severity: str = Field(default="info", pattern=r"^(info|suggestion|bug|blocker)$")
class Output(BaseModel):
    ok: bool = True
    path: str = ""
    entry_id: str = ""
def run(args: Input) -> Output:
    home = Path(os.environ.get("ROI_H_HOME") or (Path.cwd() / ".roi-h")).expanduser()
    # Prefer project feedback dir when ROI_H_PROJECT set
    project = os.environ.get("ROI_H_PROJECT")
    if project:
        out_dir = home / "projects" / project / "feedback"
    else:
        out_dir = home / "feedback"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(UTC)
    entry_id = ts.strftime("%Y%m%dT%H%M%SZ")
    entry = {
        "id": entry_id,
        "recorded_at": ts.isoformat(),
        "automation": args.automation,
        "version": args.version,
        "ok": args.ok,
        "severity": args.severity,
        "summary": args.summary,
        "suggestions": args.suggestions,
        "notes": args.notes,
        "project": project,
    }
    path = out_dir / "feedback.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=True) + "\n")
    # also one file per entry for easy reading
    single = out_dir / f"{entry_id}.json"
    single.write_text(json.dumps(entry, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return Output(ok=True, path=str(path.resolve()), entry_id=entry_id)
