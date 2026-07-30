"""browser.click"""
from __future__ import annotations
from pydantic import BaseModel, Field
TOOL_ID = "click"
DESCRIPTION = "Click by snapshot ref, visible text, or CSS selector."
DETERMINISTIC = False
REQUIRES_APPROVAL = False
TOOL_EFFECT = 'write'
IDEMPOTENCY = 'reconcile'
ALLOW_IN_PROD = True
TIMEOUT_SECONDS = 180.0
SECRET_NAMES = ()
NETWORK_HOSTS = ('*',)
FILESYSTEM_ROOTS = ()


class Input(BaseModel):
    ref: str = ""
    selector: str = ""
    text: str = ""
class Output(BaseModel):
    ok: bool = True
    ref: str = ""
    detail: str = ""
    engine: str = "playwright"
def run(args: Input) -> Output:
    import sys
    from pathlib import Path as _P
    _s = _P(__file__).resolve().parent
    if str(_s) not in sys.path:
        sys.path.insert(0, str(_s))
    from _session import click_selector, require_page, resolve_ref, use_stub
    if use_stub():
        target = args.ref or args.selector or args.text or "?"
        return Output(ok=True, ref=args.ref, detail=f"stub clicked {target}", engine="stub")
    page, handle = require_page()
    try:
        if args.ref:
            sel = resolve_ref(args.ref); click_selector(page, sel)
            return Output(ok=True, ref=args.ref, detail=f"clicked ref {args.ref} → {sel}")
        if args.text:
            page.get_by_text(args.text, exact=False).first.click()
            return Output(ok=True, detail=f"clicked text={args.text!r}")
        if args.selector:
            click_selector(page, args.selector)
            return Output(ok=True, detail=f"clicked selector={args.selector!r}")
        raise ValueError("provide ref, text, or selector")
    finally:
        handle.close_connection()
