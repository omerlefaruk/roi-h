"""browser.snapshot"""
from __future__ import annotations
from pydantic import BaseModel, Field
TOOL_ID = "snapshot"
DESCRIPTION = "Capture a page digest for planning the next action."
DETERMINISTIC = False
REQUIRES_APPROVAL = False
class Input(BaseModel):
    mode: str = Field(default="a11y", pattern=r"^(a11y|dom|text)$")
class Output(BaseModel):
    ok: bool = True
    mode: str
    text: str
    refs: list[str] = Field(default_factory=list)
    engine: str = "playwright"
def run(args: Input) -> Output:
    import sys
    from pathlib import Path as _P
    _s = _P(__file__).resolve().parent
    if str(_s) not in sys.path:
        sys.path.insert(0, str(_s))
    from _session import build_snapshot, require_page, use_stub
    if use_stub():
        return Output(ok=True, mode=args.mode, text="stub page\n  [ref=e1] button Submit\n  [ref=e2] textbox Email", refs=["e1","e2"], engine="stub")
    page, handle = require_page()
    try:
        text, refs, _ = build_snapshot(page, mode=args.mode)
        return Output(ok=True, mode=args.mode, text=text, refs=refs, engine="playwright")
    finally:
        handle.close_connection()
