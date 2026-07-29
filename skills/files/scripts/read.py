from __future__ import annotations
from pathlib import Path
from pydantic import BaseModel, Field
TOOL_ID="read"; DESCRIPTION="Read a text file."; DETERMINISTIC=True; REQUIRES_APPROVAL=False
class Input(BaseModel):
    path: str
    max_chars: int = Field(default=200_000, ge=1)
class Output(BaseModel):
    ok: bool = True
    path: str
    text: str
    bytes: int = 0
def run(args: Input) -> Output:
    p = Path(args.path).expanduser()
    raw = p.read_text(encoding="utf-8")
    if len(raw) > args.max_chars:
        raw = raw[: args.max_chars] + "\n…[truncated]"
    return Output(ok=True, path=str(p.resolve()), text=raw, bytes=p.stat().st_size)
