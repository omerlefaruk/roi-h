from __future__ import annotations
import json
import urllib.request
from pydantic import BaseModel, Field
TOOL_ID="get"; DESCRIPTION="HTTP GET; return text or JSON."; DETERMINISTIC=False; REQUIRES_APPROVAL=False
class Input(BaseModel):
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: float = 30
class Output(BaseModel):
    ok: bool = True
    status: int = 0
    text: str = ""
    json_data: dict | list | None = None
def run(args: Input) -> Output:
    req = urllib.request.Request(args.url, headers=args.headers, method="GET")
    with urllib.request.urlopen(req, timeout=args.timeout_seconds) as resp:  # noqa: S310
        body = resp.read().decode("utf-8", errors="replace")
        status = getattr(resp, "status", 200)
    data = None
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        pass
    return Output(ok=200 <= status < 400, status=status, text=body[:500_000], json_data=data)
