"""Local HTTP server for the read-only ROI-H observer."""

from __future__ import annotations

import json
import mimetypes
import sys
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

from roi_h.observer.projection import (
    ObserverLookupError,
    catalog,
    get_run,
    list_runs,
    preview_artifact,
    resolve_artifact,
)

_STATIC_ROOT = Path(__file__).with_name("static")
_SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; "
        "style-src 'self'; script-src 'self'; object-src 'self'; frame-src 'self'"
    ),
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}


class ObserverServer(ThreadingHTTPServer):
    """Threaded localhost server carrying the selected ROI-H home."""

    daemon_threads = True

    def __init__(self, address: tuple[str, int], *, home: Path) -> None:
        """Create an observer server for one resolved home."""
        super().__init__(address, ObserverHandler)
        self.home = home.resolve()


class ObserverHandler(BaseHTTPRequestHandler):
    """Serve static UI files and read-only JSON/file routes."""

    server: ObserverServer

    def do_GET(self) -> None:
        """Handle a read-only request."""
        parsed = urlparse(self.path)
        try:
            if parsed.path == "/api/catalog":
                self._send_json(catalog(self.server.home))
                return
            if parsed.path == "/api/runs":
                query = parse_qs(parsed.query)
                self._send_json(
                    {
                        "runs": list_runs(
                            self.server.home,
                            project=_optional_query(query, "project"),
                            env=_optional_query(query, "env"),
                        )
                    }
                )
                return
            if parsed.path == "/api/run":
                query = parse_qs(parsed.query)
                self._send_json(
                    get_run(
                        self.server.home,
                        project=_required_query(query, "project"),
                        env=_required_query(query, "env"),
                        run_id=_required_query(query, "run_id"),
                    )
                )
                return
            if parsed.path == "/api/artifact/preview":
                query = parse_qs(parsed.query)
                self._send_json(
                    preview_artifact(
                        self.server.home,
                        project=_required_query(query, "project"),
                        env=_required_query(query, "env"),
                        run_id=_required_query(query, "run_id"),
                        relative_path=_required_query(query, "path"),
                    )
                )
                return
            if parsed.path == "/api/artifact/file":
                self._send_artifact(parse_qs(parsed.query))
                return
            self._send_static(parsed.path)
        except ObserverLookupError as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.NOT_FOUND)
        except (OSError, TypeError, ValueError) as exc:
            self._send_json({"error": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:  # noqa: BLE001
            self._send_json(
                {"error": f"Observer failed to read this resource: {exc}"},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def do_POST(self) -> None:
        """Reject mutations explicitly."""
        self._send_json(
            {"error": "ROI-H Observer is read only."},
            status=HTTPStatus.METHOD_NOT_ALLOWED,
        )

    do_PUT = do_POST  # noqa: N815
    do_PATCH = do_POST  # noqa: N815
    do_DELETE = do_POST  # noqa: N815

    def log_message(self, message_format: str, *args: object) -> None:
        """Keep terminal output compact while retaining request failures."""
        if args and str(args[1]).startswith(("4", "5")):
            super().log_message(message_format, *args)

    def _send_json(
        self,
        payload: dict[str, Any],
        *,
        status: HTTPStatus = HTTPStatus.OK,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self._send_security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_static(self, request_path: str) -> None:
        relative = "index.html" if request_path in {"", "/"} else request_path.lstrip("/")
        path = (_STATIC_ROOT / relative).resolve()
        if not path.is_relative_to(_STATIC_ROOT.resolve()) or not path.is_file():
            self._send_json({"error": "Not found"}, status=HTTPStatus.NOT_FOUND)
            return
        content_type = {
            ".css": "text/css; charset=utf-8",
            ".html": "text/html; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".svg": "image/svg+xml",
        }.get(path.suffix.lower(), "application/octet-stream")
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        self._send_security_headers()
        self.end_headers()
        self.wfile.write(body)

    def _send_artifact(self, query: dict[str, list[str]]) -> None:
        path, _artifact_root = resolve_artifact(
            self.server.home,
            project=_required_query(query, "project"),
            env=_required_query(query, "env"),
            run_id=_required_query(query, "run_id"),
            relative_path=_required_query(query, "path"),
        )
        download = _optional_query(query, "download") == "1"
        content_type = _content_type(path)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        disposition = "attachment" if download else "inline"
        self.send_header(
            "Content-Disposition",
            f"{disposition}; filename*=UTF-8''{_quote_filename(path.name)}",
        )
        self.send_header("Content-Length", str(path.stat().st_size))
        self._send_security_headers()
        self.end_headers()
        with path.open("rb") as handle:
            while chunk := handle.read(65_536):
                self.wfile.write(chunk)

    def _send_security_headers(self) -> None:
        for name, value in _SECURITY_HEADERS.items():
            self.send_header(name, value)


def serve_observer(
    home: Path,
    *,
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    """Run the observer on localhost until interrupted."""
    if not home.is_dir():
        msg = f"ROI-H home does not exist: {home}"
        raise FileNotFoundError(msg)
    server = ObserverServer(("127.0.0.1", port), home=home)
    actual_port = int(server.server_address[1])
    url = f"http://127.0.0.1:{actual_port}/"
    sys.stdout.write(f"ROI-H Observer: {url}\n")
    sys.stdout.write(f"Home: {home.resolve()}\n")
    sys.stdout.write("Read only. Press Ctrl-C to stop.\n")
    sys.stdout.flush()
    if open_browser:
        webbrowser.open_new_tab(url)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _required_query(query: dict[str, list[str]], name: str) -> str:
    value = _optional_query(query, name)
    if value is None:
        msg = f"missing query parameter: {name}"
        raise ValueError(msg)
    return value


def _optional_query(query: dict[str, list[str]], name: str) -> str | None:
    values = query.get(name)
    if not values or not values[0]:
        return None
    return values[0]


def _quote_filename(value: str) -> str:
    return quote(value, safe="")


def _content_type(path: Path) -> str:
    return mimetypes.guess_type(path.name)[0] or "application/octet-stream"


__all__ = ["ObserverHandler", "ObserverServer", "serve_observer"]
