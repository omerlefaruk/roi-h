"""browser.fill"""
from __future__ import annotations
from pydantic import BaseModel, Field
TOOL_ID = "fill"
DESCRIPTION = "Fill an input by stable label (preferred), ref, or selector."
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
    value: str
    label: str = ""
    ref: str = ""
    selector: str = ""
class Output(BaseModel):
    ok: bool = True
    how: str = ""
    value_len: int = 0
    engine: str = "playwright"
def run(args: Input) -> Output:
    import sys
    from pathlib import Path as _P
    _s = _P(__file__).resolve().parent
    if str(_s) not in sys.path:
        sys.path.insert(0, str(_s))
    from _session import fill_by_label, fill_selector, require_page, resolve_ref, use_stub
    if use_stub():
        how = args.label or args.ref or args.selector or "?"
        return Output(ok=True, how=f"stub fill {how}", value_len=len(args.value), engine="stub")
    page, handle = require_page()
    try:
        if args.label:
            return Output(ok=True, how=fill_by_label(page, args.label, args.value), value_len=len(args.value))
        if args.ref:
            fill_selector(page, resolve_ref(args.ref), args.value)
            return Output(ok=True, how=f"ref={args.ref}", value_len=len(args.value))
        if args.selector:
            fill_selector(page, args.selector, args.value)
            return Output(ok=True, how=f"selector={args.selector}", value_len=len(args.value))
        raise ValueError("provide label, ref, or selector")
    finally:
        handle.close_connection()
