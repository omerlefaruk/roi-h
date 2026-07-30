"""browser.download"""
from __future__ import annotations
from pathlib import Path
from pydantic import BaseModel, Field
TOOL_ID = "download"
DESCRIPTION = "Click a download link/button and save the file; returns path."
DETERMINISTIC = False
REQUIRES_APPROVAL = False
FILESYSTEM_ROOTS = ("run:work:read-write", "run:output:read-write", "run:tmp:read-write")
TOOL_EFFECT = 'write'
IDEMPOTENCY = 'reconcile'
ALLOW_IN_PROD = True
TIMEOUT_SECONDS = 180.0
SECRET_NAMES = ()
NETWORK_HOSTS = ('*',)


class Input(BaseModel):
    text: str = Field(default="", description="Visible link/button text to click")
    ref: str = ""
    selector: str = ""
    path: str = Field(default="", description="Destination path (default: ./download.bin)")
    timeout_ms: int = 30000
class Output(BaseModel):
    ok: bool = True
    path: str = ""
    suggested_filename: str = ""
    engine: str = "playwright"
def run(args: Input) -> Output:
    import sys
    from pathlib import Path as _P
    _s = _P(__file__).resolve().parent
    if str(_s) not in sys.path:
        sys.path.insert(0, str(_s))
    from _session import click_selector, require_page, resolve_ref, use_stub
    if use_stub():
        dest = Path(args.path or "download.bin")
        dest.write_bytes(b"stub")
        return Output(ok=True, path=str(dest.resolve()), suggested_filename=dest.name, engine="stub")
    page, handle = require_page()
    try:
        with page.expect_download(timeout=args.timeout_ms) as dl_info:
            if args.ref:
                click_selector(page, resolve_ref(args.ref))
            elif args.text:
                page.get_by_text(args.text, exact=False).first.click()
            elif args.selector:
                click_selector(page, args.selector)
            else:
                raise ValueError("provide text, ref, or selector")
        download = dl_info.value
        dest = Path(args.path or download.suggested_filename or "download.bin")
        dest.parent.mkdir(parents=True, exist_ok=True)
        download.save_as(str(dest))
        return Output(ok=True, path=str(dest.resolve()), suggested_filename=download.suggested_filename or dest.name)
    finally:
        handle.close_connection()
