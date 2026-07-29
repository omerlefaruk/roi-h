from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, Field

TOOL_ID = "list"
DESCRIPTION = "List recent automation feedback entries for the current run."
DETERMINISTIC = True
REQUIRES_APPROVAL = False


class Input(BaseModel):
    limit: int = Field(default=20, ge=1, le=500)


class Output(BaseModel):
    ok: bool = True
    entries: list[dict]
    count: int


def run(args: Input) -> Output:
    database = Path(os.environ.get("ROI_H_DB", ""))
    run_id = os.environ.get("ROI_H_RUN_ID")
    if not database.is_file() or not run_id:
        return Output(ok=True, entries=[], count=0)
    from roi_h.observer.activegraph_adapter import ActiveGraphProjectionAdapter

    projection = ActiveGraphProjectionAdapter(database).project_run(run_id)
    entries = [
        dict(item.get("data") or {})
        for item in projection["objects"]
        if item.get("type") == "rpa.feedback"
    ]
    entries = entries[-args.limit :]
    return Output(ok=True, entries=entries, count=len(entries))
