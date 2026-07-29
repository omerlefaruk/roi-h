"""browser.screenshot"""
from __future__ import annotations
from pathlib import Path
from pydantic import BaseModel, Field
TOOL_ID = "screenshot"
DESCRIPTION = "Capture a PNG screenshot of the current page; returns path."
DETERMINISTIC = False
REQUIRES_APPROVAL = False
FILESYSTEM_ROOTS = ("run:work:read-write", "run:output:read-write", "run:tmp:read-write")
class Input(BaseModel):
    path: str = Field(default="screenshot.png")
    full_page: bool = True
class Output(BaseModel):
    ok: bool = True
    path: str = ""
    engine: str = "playwright"
def run(args: Input) -> Output:
    import sys
    from pathlib import Path as _P
    _s = _P(__file__).resolve().parent
    if str(_s) not in sys.path:
        sys.path.insert(0, str(_s))
    from _session import require_page, use_stub
    dest = Path(args.path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if use_stub():
        dest.write_bytes(b"\x89PNG\r\n\x1a\nstub")
        return Output(ok=True, path=str(dest.resolve()), engine="stub")
    page, handle = require_page()
    try:
        page.screenshot(path=str(dest), full_page=args.full_page)
        return Output(ok=True, path=str(dest.resolve()))
    finally:
        handle.close_connection()
