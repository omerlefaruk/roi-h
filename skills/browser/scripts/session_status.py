"""browser.session_status"""
from __future__ import annotations
from pydantic import BaseModel
TOOL_ID = "session_status"
DESCRIPTION = "Report whether the shared Chromium CDP session is alive."
DETERMINISTIC = False
REQUIRES_APPROVAL = False
TOOL_EFFECT = 'read'
IDEMPOTENCY = 'none'
ALLOW_IN_PROD = True
TIMEOUT_SECONDS = 180.0
SECRET_NAMES = ()
NETWORK_HOSTS = ('*',)
FILESYSTEM_ROOTS = ()


class Input(BaseModel):
    pass
class Output(BaseModel):
    ok: bool = True
    alive: bool = False
    pid: int | None = None
    endpoint: str | None = None
    headed: bool | None = None
    last_url: str | None = None
    last_title: str | None = None
    ref_count: int = 0
def run(args: Input) -> Output:
    del args
    import sys
    from pathlib import Path as _P
    _s = _P(__file__).resolve().parent
    if str(_s) not in sys.path:
        sys.path.insert(0, str(_s))
    from _session import session_status, use_stub
    if use_stub():
        return Output(ok=True, alive=False)
    data = session_status()
    return Output(**data)
