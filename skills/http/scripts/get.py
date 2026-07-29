from __future__ import annotations
import gzip
import json
import ssl
import urllib.error
import urllib.request
from typing import Literal
from pydantic import BaseModel, Field, field_validator
TOOL_ID="get"; DESCRIPTION="HTTP GET; return text or JSON."; DETERMINISTIC=False; REQUIRES_APPROVAL=False
class Input(BaseModel):
    url: str
    headers: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: float = 30

    @field_validator("url")
    @classmethod
    def _http_url(cls, value: str) -> str:
        url = value.strip()
        if not (url.startswith("http://") or url.startswith("https://")):
            raise ValueError("url must start with http:// or https://")
        return url
class RemoteError(BaseModel):
    category: Literal["remote_http", "remote_network", "remote_tls"]
    code: str
    message: str
    retryable: bool = False
class Output(BaseModel):
    ok: bool = True
    status: int = 0
    text: str = ""
    json_data: dict | list | None = None
    error: RemoteError | None = None
def run(args: Input) -> Output:
    headers = {"Accept-Encoding": "identity", **args.headers}
    req = urllib.request.Request(args.url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=args.timeout_seconds) as resp:  # noqa: S310
            body = _read_body(resp)
            status = getattr(resp, "status", 200)
    except urllib.error.HTTPError as exc:
        body = _read_body(exc)
        return _response(
            body,
            status=exc.code,
            error=RemoteError(
                category="remote_http",
                code="http.remote_response",
                message=f"Remote server returned HTTP {exc.code}.",
                retryable=exc.code in {408, 425, 429} or exc.code >= 500,
            ),
        )
    except urllib.error.URLError as exc:
        return _remote_failure(exc.reason)
    except ssl.SSLError as exc:
        return _remote_failure(exc)
    except TimeoutError as exc:
        return Output(
            ok=False,
            error=RemoteError(
                category="remote_network",
                code="http.remote_timeout",
                message=str(exc)[:1_000] or "The remote request timed out.",
                retryable=True,
            ),
        )
    except OSError as exc:
        return _remote_failure(exc)
    return _response(body, status=status)

def _read_body(response: object) -> str:
    raw = response.read()
    headers = getattr(response, "headers", {})
    content_encoding = str(headers.get("Content-Encoding", "")).lower()
    if "gzip" in content_encoding:
        try:
            raw = gzip.decompress(raw)
        except OSError:
            pass
    charset = None
    get_content_charset = getattr(headers, "get_content_charset", None)
    if callable(get_content_charset):
        charset = get_content_charset()
    return raw.decode(charset or "utf-8", errors="replace")

def _response(body: str, *, status: int, error: RemoteError | None = None) -> Output:
    data = None
    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        pass
    return Output(
        ok=error is None and 200 <= status < 400,
        status=status,
        text=body[:500_000],
        json_data=data,
        error=error,
    )

def _remote_failure(reason: object) -> Output:
    is_tls = isinstance(reason, ssl.SSLError)
    is_certificate = isinstance(reason, ssl.SSLCertVerificationError)
    return Output(
        ok=False,
        error=RemoteError(
            category="remote_tls" if is_tls else "remote_network",
            code="http.remote_tls_error" if is_tls else "http.remote_network_error",
            message=str(reason)[:1_000] or "The remote connection failed.",
            retryable=not is_certificate,
        ),
    )
