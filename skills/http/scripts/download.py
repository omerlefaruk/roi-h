from __future__ import annotations
import urllib.request
from pathlib import Path
from pydantic import BaseModel, Field
TOOL_ID="download"; DESCRIPTION="Download a URL to a local path."; DETERMINISTIC=False; REQUIRES_APPROVAL=False
class Input(BaseModel):
    url: str
    path: str
    headers: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: float = 60
class Output(BaseModel):
    ok: bool = True
    path: str
    bytes: int = 0
def run(args: Input) -> Output:
    dest = Path(args.path).expanduser()
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(args.url, headers=args.headers)
    with urllib.request.urlopen(req, timeout=args.timeout_seconds) as resp:  # noqa: S310
        data = resp.read()
    dest.write_bytes(data)
    return Output(ok=True, path=str(dest.resolve()), bytes=len(data))
