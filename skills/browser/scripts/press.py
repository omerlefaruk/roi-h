"""browser.press"""
from __future__ import annotations
from pydantic import BaseModel
TOOL_ID = "press"
DESCRIPTION = "Press a keyboard key (Enter, Tab, Escape, …)."
DETERMINISTIC = False
REQUIRES_APPROVAL = False
class Input(BaseModel):
    key: str
class Output(BaseModel):
    ok: bool = True
    key: str
    engine: str = "playwright"
def run(args: Input) -> Output:
    import sys
    from pathlib import Path as _P
    _s = _P(__file__).resolve().parent
    if str(_s) not in sys.path:
        sys.path.insert(0, str(_s))
    from _session import require_page, use_stub
    if use_stub():
        return Output(ok=True, key=args.key, engine="stub")
    page, handle = require_page()
    try:
        page.keyboard.press(args.key)
        return Output(ok=True, key=args.key)
    finally:
        handle.close_connection()
