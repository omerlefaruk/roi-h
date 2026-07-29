from __future__ import annotations
import shutil
from pathlib import Path
from pydantic import BaseModel
TOOL_ID="copy"; DESCRIPTION="Copy a file."; DETERMINISTIC=True; REQUIRES_APPROVAL=False
class Input(BaseModel):
    source: str
    dest: str
class Output(BaseModel):
    ok: bool = True
    path: str
    source: str
def run(args: Input) -> Output:
    src, dest = Path(args.source).expanduser(), Path(args.dest).expanduser()
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dest)
    return Output(ok=True, path=str(dest.resolve()), source=str(src.resolve()))
