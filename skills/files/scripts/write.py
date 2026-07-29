from __future__ import annotations
from pathlib import Path
from pydantic import BaseModel
TOOL_ID="write"; DESCRIPTION="Write a text file."; DETERMINISTIC=True; REQUIRES_APPROVAL=False
FILESYSTEM_ROOTS=("run:work:read-write","run:output:read-write","run:tmp:read-write")
class Input(BaseModel):
    path: str
    text: str = ""
class Output(BaseModel):
    ok: bool = True
    path: str
    bytes: int = 0
def run(args: Input) -> Output:
    p = Path(args.path).expanduser()
    p.parent.mkdir(parents=True, exist_ok=True)
    data = args.text.encode("utf-8")
    p.write_bytes(data)
    return Output(ok=True, path=str(p.resolve()), bytes=len(data))
