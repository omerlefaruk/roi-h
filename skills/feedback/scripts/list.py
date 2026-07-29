from __future__ import annotations
import json
import os
from pathlib import Path
from pydantic import BaseModel, Field
TOOL_ID="list"
DESCRIPTION="List recent automation feedback entries."
DETERMINISTIC=True
REQUIRES_APPROVAL=False
class Input(BaseModel):
    limit: int = Field(default=20, ge=1, le=500)
class Output(BaseModel):
    ok: bool = True
    path: str = ""
    entries: list[dict]
    count: int
def run(args: Input) -> Output:
    home = Path(os.environ.get("ROI_H_HOME") or (Path.cwd() / ".roi-h")).expanduser()
    project = os.environ.get("ROI_H_PROJECT")
    path = (home / "projects" / project / "feedback" / "feedback.jsonl") if project else (home / "feedback" / "feedback.jsonl")
    if not path.is_file():
        return Output(ok=True, path=str(path), entries=[], count=0)
    lines = path.read_text(encoding="utf-8").splitlines()
    entries = []
    for line in lines[-args.limit:]:
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return Output(ok=True, path=str(path.resolve()), entries=entries, count=len(entries))
