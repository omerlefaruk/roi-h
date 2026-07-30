from __future__ import annotations
import hashlib
from pathlib import Path
from pydantic import BaseModel
TOOL_ID="hash"; DESCRIPTION="SHA-256 of a file."; DETERMINISTIC=True; REQUIRES_APPROVAL=False
FILESYSTEM_ROOTS=("project:reference:read","run:input:read","run:work:read-write","run:output:read-write","artifact:read","automation:read")
TOOL_EFFECT = 'read'
IDEMPOTENCY = 'none'
ALLOW_IN_PROD = True
TIMEOUT_SECONDS = 120.0
SECRET_NAMES = ()
NETWORK_HOSTS = ()


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
