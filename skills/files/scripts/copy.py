from __future__ import annotations
import shutil
from pathlib import Path
from pydantic import BaseModel
TOOL_ID="copy"; DESCRIPTION="Copy a file."; DETERMINISTIC=True; REQUIRES_APPROVAL=False
FILESYSTEM_ROOTS=("project:reference:read","run:input:read","run:work:read-write","run:output:read-write","run:tmp:read-write","artifact:read","automation:read")
TOOL_EFFECT = 'write'
IDEMPOTENCY = 'reconcile'
ALLOW_IN_PROD = True
TIMEOUT_SECONDS = 120.0
SECRET_NAMES = ()
NETWORK_HOSTS = ()


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
