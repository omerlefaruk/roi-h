from __future__ import annotations
import json
import urllib.request
from pydantic import BaseModel, Field
TOOL_ID="post"; DESCRIPTION="HTTP POST JSON body."; DETERMINISTIC=False; REQUIRES_APPROVAL=False
class Input(BaseModel):
    url: str
    json_body: dict | list | None = None
    body: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: float = 30
class Output(BaseModel):
    ok: bool = True
    status: int = 0
    text: str = ""
def run(args: Input) -> Output:
    data = args.body.encode() if args.body else json.dumps(args.json_body or {}).encode()
    headers = {"Content-Type": "application/json", **args.headers}
    req = urllib.request.Request(args.url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=args.timeout_seconds) as resp:  # noqa: S310
        body = resp.read().decode("utf-8", errors="replace")
        status = getattr(resp, "status", 200)
    return Output(ok=200 <= status < 400, status=status, text=body[:500_000])
