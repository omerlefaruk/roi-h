"""browser.select"""
from __future__ import annotations
from pydantic import BaseModel
TOOL_ID = "select"
DESCRIPTION = "Select an option on a <select> by label/ref/selector and option value or label."
DETERMINISTIC = False
REQUIRES_APPROVAL = False
class Input(BaseModel):
    value: str = ""
    label: str = ""
    option_label: str = ""
    ref: str = ""
    selector: str = ""
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
    from _session import require_page, resolve_ref, use_stub
    if use_stub():
        return Output(ok=True, how="stub select", engine="stub")
    page, handle = require_page()
    try:
        if args.label:
            loc = page.get_by_label(args.label, exact=False)
        elif args.ref:
            loc = page.locator(resolve_ref(args.ref)).first
        elif args.selector:
            loc = page.locator(args.selector).first
        else:
            raise ValueError("provide label, ref, or selector")
        if args.option_label:
            loc.select_option(label=args.option_label)
            how = f"label={args.option_label}"
        else:
            loc.select_option(args.value)
            how = f"value={args.value}"
        return Output(ok=True, how=how)
    finally:
        handle.close_connection()
