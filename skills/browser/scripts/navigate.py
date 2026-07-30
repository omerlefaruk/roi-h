"""browser.navigate"""
from __future__ import annotations
from pydantic import BaseModel, Field, field_validator
TOOL_ID = "navigate"
DESCRIPTION = "Navigate the browser to a URL."
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
    url: str = Field(min_length=1)
    headless: bool | None = Field(default=None, description="Override session headed mode on first launch")

    @field_validator("url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        v = value.strip()
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("url must start with http:// or https://")
        return v
class Output(BaseModel):
    ok: bool = True
    url: str
    title: str = ""
    mode: str = "playwright"
def run(args: Input) -> Output:
    import sys
    from pathlib import Path as _P
    _s = _P(__file__).resolve().parent
    if str(_s) not in sys.path:
        sys.path.insert(0, str(_s))
    from _session import require_page, use_stub, want_headed
    if use_stub():
        return Output(ok=True, url=args.url, title=f"stub title for {args.url}", mode="stub")
    headed = None if args.headless is None else (not args.headless)
    if headed is None and want_headed():
        headed = True
    page, handle = require_page(headed=headed)
    try:
        page.goto(args.url, wait_until="domcontentloaded")
        return Output(ok=True, url=page.url, title=page.title() or "", mode="playwright")
    finally:
        handle.close_connection()
