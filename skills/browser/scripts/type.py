"""browser.type — keystrokes (append) into focused or labeled field."""
from __future__ import annotations
from pydantic import BaseModel, Field
TOOL_ID = "type"
DESCRIPTION = "Type text into a field by label/ref/selector (appends; use fill to replace)."
DETERMINISTIC = False
REQUIRES_APPROVAL = False
class Input(BaseModel):
    text: str
    label: str = ""
    ref: str = ""
    selector: str = ""
    delay_ms: int = Field(default=0, ge=0, le=500)
class Output(BaseModel):
    ok: bool = True
    how: str = ""
    engine: str = "playwright"
def run(args: Input) -> Output:
    import sys
    from pathlib import Path as _P
    _s = _P(__file__).resolve().parent
    if str(_s) not in sys.path:
        sys.path.insert(0, str(_s))
    from _session import fill_by_label, require_page, resolve_ref, use_stub
    if use_stub():
        return Output(ok=True, how="stub type", engine="stub")
    page, handle = require_page()
    try:
        if args.label:
            # focus via fill empty then type — use locator type
            how = fill_by_label(page, args.label, "")  # focus/clear soft
            # re-locate and type
            page.keyboard.type(args.text, delay=args.delay_ms)
            return Output(ok=True, how=how)
        sel = resolve_ref(args.ref) if args.ref else args.selector
        if not sel:
            raise ValueError("provide label, ref, or selector")
        loc = page.locator(sel).first
        loc.click()
        loc.type(args.text, delay=args.delay_ms)
        return Output(ok=True, how=sel)
    finally:
        handle.close_connection()
