from __future__ import annotations
import hashlib
from pathlib import Path
from pydantic import BaseModel
TOOL_ID="hash"; DESCRIPTION="SHA-256 of a file."; DETERMINISTIC=True; REQUIRES_APPROVAL=False
class Input(BaseModel):
    path: str
class Output(BaseModel):
    ok: bool = True
    path: str
    sha256: str
    bytes: int
def run(args: Input) -> Output:
    p = Path(args.path).expanduser()
    h = hashlib.sha256(p.read_bytes()).hexdigest()
    return Output(ok=True, path=str(p.resolve()), sha256=h, bytes=p.stat().st_size)
