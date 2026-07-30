from __future__ import annotations
from pathlib import Path
from pydantic import BaseModel, Field
TOOL_ID="glob"; DESCRIPTION="List files matching a glob pattern."; DETERMINISTIC=True; REQUIRES_APPROVAL=False
FILESYSTEM_ROOTS=("project:reference:read","run:input:read","run:work:read-write","run:output:read-write","artifact:read","automation:read")
TOOL_EFFECT = 'read'
IDEMPOTENCY = 'none'
ALLOW_IN_PROD = True
TIMEOUT_SECONDS = 120.0
SECRET_NAMES = ()
NETWORK_HOSTS = ()


class Input(BaseModel):
    pattern: str
    root: str = "."
    max_results: int = Field(default=200, ge=1, le=5000)
class Output(BaseModel):
    ok: bool = True
    paths: list[str]
    count: int
def run(args: Input) -> Output:
    root = Path(args.root).expanduser()
    paths = [str(p.resolve()) for p in sorted(root.glob(args.pattern))[: args.max_results]]
    return Output(ok=True, paths=paths, count=len(paths))
