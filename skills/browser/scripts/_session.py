"""Shared Playwright browser session (CDP) for multi-process CLI invokes."""

from __future__ import annotations

import contextlib
import json
import os
import re
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

_STUB_ENV = "ROI_H_BROWSER"
_STATE_ENV = "ROI_H_BROWSER_STATE"
_HEADED_ENV = "ROI_H_BROWSER_HEADED"
_SLOW_ENV = "ROI_H_BROWSER_SLOW_MO"
_CONNECT_TIMEOUT_ENV = "ROI_H_BROWSER_CONNECT_TIMEOUT_MS"


def use_stub() -> bool:
    flag = os.environ.get(_STUB_ENV, "").strip().lower()
    if flag in {"stub", "0", "false", "no"}:
        return True
    if flag in {"playwright", "real", "1", "true", "yes"}:
        return False
    try:
        import playwright  # noqa: F401
    except ImportError:
        return True
    return False


def want_headed() -> bool:
    return os.environ.get(_HEADED_ENV, "").strip().lower() in {"1", "true", "yes", "headed"}


def slow_mo_ms() -> int:
    raw = os.environ.get(_SLOW_ENV, "").strip()
    if not raw:
        return 0
    try:
        return max(0, int(raw))
    except ValueError:
        return 0


def connect_timeout_ms() -> int:
    raw = os.environ.get(_CONNECT_TIMEOUT_ENV, "").strip()
    if not raw:
        return 10_000
    try:
        return max(1_000, int(raw))
    except ValueError:
        return 10_000


def state_path() -> Path:
    env = os.environ.get(_STATE_ENV)
    if env:
        return Path(env).expanduser().resolve()
    home = os.environ.get("ROI_H_HOME")
    if home:
        root = Path(home).expanduser().resolve()
        project = os.environ.get("ROI_H_PROJECT", "").strip()
        if project:
            return root / "projects" / project / "browser-session.json"
        return root / "browser-session.json"
    return (Path.cwd() / ".roi-h" / "browser-session.json").resolve()


def profile_dir() -> Path:
    """Persistent profile owned by the ROI-H shared browser session."""
    return state_path().with_name("browser-profile")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _read_state() -> dict[str, Any]:
    path = state_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_state(data: dict[str, Any]) -> None:
    path = state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _session_details(state: dict[str, Any]) -> tuple[str, int, bool] | None:
    endpoint = state.get("endpoint")
    pid = state.get("pid")
    headed = state.get("headed")
    if (
        not isinstance(endpoint, str)
        or not endpoint.strip()
        or not isinstance(pid, int)
        or isinstance(pid, bool)
        or pid <= 0
        or not isinstance(headed, bool)
    ):
        return None
    return endpoint.strip(), pid, headed


def _recorded_pid(state: dict[str, Any]) -> int:
    pid = state.get("pid")
    return pid if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0 else 0


def _chromium_executable() -> str:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        return p.chromium.executable_path


def _pid_alive(pid: int) -> bool:
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        process_query_limited_information = 0x1000
        still_active = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
        if not handle:
            return False
        try:
            exit_code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return False
            return exit_code.value == still_active
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _launch_chromium(port: int, *, headed: bool) -> int:
    exe = _chromium_executable()
    profile = profile_dir()
    profile.mkdir(parents=True, exist_ok=True)
    args = [
        exe,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "about:blank",
    ]
    if not headed:
        args.insert(1, "--headless=new")
        args.insert(2, "--disable-gpu")
    proc = subprocess.Popen(  # noqa: S603
        args,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    deadline = time.time() + 15
    while time.time() < deadline:
        if proc.poll() is not None:
            msg = f"chromium exited early with code {proc.returncode}"
            raise RuntimeError(msg)
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.3):
                return proc.pid
        except OSError:
            time.sleep(0.1)
    proc.kill()
    msg = f"chromium CDP did not open on port {port}"
    raise TimeoutError(msg)


def _stop_pid(pid: int) -> bool:
    if not _pid_alive(pid):
        return False
    try:
        os.kill(pid, 15)
    except OSError:
        return False
    time.sleep(0.2)
    if _pid_alive(pid):
        with contextlib.suppress(OSError):
            os.kill(pid, 9)
    return True


def _start_session(*, headed: bool) -> tuple[str, int]:
    port = _free_port()
    pid = _launch_chromium(port, headed=headed)
    endpoint = f"http://127.0.0.1:{port}"
    try:
        _write_state(
            {
                "endpoint": endpoint,
                "pid": pid,
                "headed": headed,
                "ref_map": {},
                "last_url": "",
                "last_title": "",
            }
        )
    except Exception:
        with contextlib.suppress(Exception):
            _stop_pid(pid)
        raise
    return endpoint, pid


class LiveBrowser:
    def __init__(self) -> None:
        self._pw: Any = None
        self.browser: Any = None
        self.context: Any = None
        self.page: Any = None
        self.endpoint: str = ""

    def connect(
        self,
        *,
        headed: bool | None = None,
        timeout_ms: int | None = None,
    ) -> None:
        from playwright.sync_api import sync_playwright

        state = _read_state()
        details = _session_details(state)
        requested_headed = want_headed() if headed is None else headed
        connect_timeout = timeout_ms or connect_timeout_ms()
        if details is not None:
            endpoint, pid, recorded_headed = details
            if not _pid_alive(pid) or recorded_headed != requested_headed:
                session_stop()
                endpoint = ""
        else:
            # Missing, malformed, and incomplete state are disposable. If the
            # valid part names one process, session_stop limits cleanup to it.
            session_stop()
            endpoint = ""

        for attempt in range(2):
            try:
                if not endpoint:
                    endpoint, _ = _start_session(headed=requested_headed)
                self._pw = sync_playwright().start()
                self.browser = self._pw.chromium.connect_over_cdp(
                    endpoint,
                    timeout=connect_timeout,
                )
                break
            except Exception as exc:
                # Recreate only the process in ROI-H's current state file. This
                # also retries a first CDP failure after a fresh launch.
                self.close_connection()
                session_stop()
                endpoint = ""
                if attempt == 1:
                    msg = f"browser.session_unavailable: {type(exc).__name__}: {exc}"
                    raise RuntimeError(msg) from exc
        self.endpoint = endpoint
        if self.browser.contexts:
            self.context = self.browser.contexts[0]
        else:
            self.context = self.browser.new_context(accept_downloads=True)
        pages = list(self.context.pages)
        self.page = _select_page(pages) or self.context.new_page()
        self.page.set_default_timeout(30_000)
        slow = slow_mo_ms()
        if slow:
            # best-effort: set default timeout higher when slow-mo is on
            self.page.set_default_timeout(30_000 + slow * 20)

    def close_connection(self) -> None:
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:  # noqa: BLE001
                pass
        self._pw = None
        self.browser = None
        self.context = None
        self.page = None


def _select_page(pages: list[Any]) -> Any | None:
    """Prefer the newest usable restored page without portal-specific knowledge."""
    usable = [
        page
        for page in pages
        if str(getattr(page, "url", "")).strip()
        and str(getattr(page, "url", "")).lower() != "about:blank"
        and not str(getattr(page, "url", "")).lower().startswith("chrome-error://")
    ]
    if usable:
        return usable[-1]
    return pages[-1] if pages else None


def require_page(
    *,
    headed: bool | None = None,
    timeout_ms: int | None = None,
) -> tuple[Any, LiveBrowser]:
    handle = LiveBrowser()
    handle.connect(headed=headed, timeout_ms=timeout_ms)
    assert handle.page is not None
    return handle.page, handle


def session_status() -> dict[str, Any]:
    state = _read_state()
    pid = _recorded_pid(state)
    alive = bool(pid and _pid_alive(pid))
    return {
        "ok": True,
        "alive": alive,
        "pid": pid or None,
        "endpoint": state.get("endpoint"),
        "headed": state.get("headed"),
        "last_url": state.get("last_url"),
        "last_title": state.get("last_title"),
        "ref_count": len(state.get("ref_map") or {}),
    }


def session_stop() -> dict[str, Any]:
    state = _read_state()
    pid = _recorded_pid(state)
    killed = bool(pid and _stop_pid(pid))
    path = state_path()
    if path.is_file():
        path.unlink()
    return {"ok": True, "stopped": True, "pid": pid or None, "killed": killed}


def save_ref_map(ref_map: dict[str, str], *, url: str = "", title: str = "") -> None:
    state = _read_state()
    state["ref_map"] = ref_map
    if url:
        state["last_url"] = url
    if title:
        state["last_title"] = title
    _write_state(state)


def load_ref_map() -> dict[str, str]:
    state = _read_state()
    raw = state.get("ref_map") or {}
    return {str(k): str(v) for k, v in raw.items()}


def build_snapshot(page: Any, *, mode: str = "a11y") -> tuple[str, list[str], dict[str, str]]:
    lines: list[str] = []
    refs: list[str] = []
    ref_map: dict[str, str] = {}
    n = 0
    title = ""
    try:
        title = page.title() or ""
    except Exception:  # noqa: BLE001
        title = ""
    url = ""
    try:
        url = page.url or ""
    except Exception:  # noqa: BLE001
        url = ""
    lines.append(f"url: {url}")
    lines.append(f"title: {title}")

    if mode == "text":
        body = page.inner_text("body")
        clipped = body if len(body) <= 8000 else body[:8000] + "\n…[truncated]"
        lines.append(clipped)
        save_ref_map({}, url=url, title=title)
        return "\n".join(lines), [], {}

    labeled = page.evaluate(
        """() => {
          const out = [];
          const inputs = document.querySelectorAll('input, textarea, select, button, a[href]');
          for (const el of inputs) {
            const tag = el.tagName.toLowerCase();
            let role = tag;
            if (tag === 'input') role = el.type || 'textbox';
            if (tag === 'button' || (tag === 'input' && (el.type === 'submit' || el.type === 'button')))
              role = 'button';
            if (tag === 'a') role = 'link';
            if (tag === 'textarea') role = 'textbox';
            if (tag === 'select') role = 'combobox';
            let name = '';
            if (el.labels && el.labels.length) name = el.labels[0].innerText.trim();
            if (!name) name = el.getAttribute('ng-reflect-name') || '';
            if (!name) name = el.getAttribute('placeholder') || '';
            if (!name) name = el.getAttribute('aria-label') || '';
            if (!name) name = (el.innerText || el.value || el.name || '').trim();
            name = (name || '').replace(/\\s+/g, ' ').slice(0, 120);
            let selector = '';
            if (el.getAttribute('ng-reflect-name')) {
              selector = '[ng-reflect-name="' + el.getAttribute('ng-reflect-name') + '"]';
            } else if (el.id) {
              selector = '#' + el.id;
            } else if (el.name) {
              selector = tag + '[name="' + el.name + '"]';
            } else if (name && (role === 'button' || role === 'link')) {
              selector = 'text=' + name;
            } else {
              selector = tag;
            }
            out.push({ role, name, selector, value: el.value || '' });
          }
          return out;
        }"""
    )
    for item in labeled:
        n += 1
        ref = f"e{n}"
        role = str(item.get("role") or "generic")
        name = str(item.get("name") or "")
        selector = str(item.get("selector") or "")
        value = str(item.get("value") or "")
        ref_map[ref] = selector
        refs.append(ref)
        extra = f' value="{value}"' if value and role in {"textbox", "text", "email", "tel"} else ""
        label = f' "{name}"' if name else ""
        lines.append(f"  [{ref}] {role}{label}{extra}  css={selector}")

    save_ref_map(ref_map, url=url, title=title)
    return "\n".join(lines), refs, ref_map


def resolve_ref(ref: str) -> str:
    ref_map = load_ref_map()
    if ref not in ref_map:
        msg = f"unknown ref {ref!r}; call browser.snapshot first"
        raise KeyError(msg)
    return ref_map[ref]


def click_selector(page: Any, selector: str) -> None:
    if selector.startswith("text="):
        page.get_by_text(selector[5:], exact=False).first.click()
        return
    if selector.startswith("role="):
        m = re.match(r'role=(\w+)(?:\[name="(.*)"\])?(?:>> nth=(\d+))?', selector)
        if not m:
            page.locator(selector).first.click()
            return
        role, name, nth = m.group(1), m.group(2), m.group(3)
        loc = page.get_by_role(role, name=name) if name else page.get_by_role(role)
        if nth is not None:
            loc.nth(int(nth)).click()
        else:
            loc.first.click()
        return
    page.locator(selector).first.click()


def fill_selector(page: Any, selector: str, value: str) -> None:
    page.locator(selector).first.fill(value)


def fill_by_label(page: Any, label: str, value: str) -> str:
    attr = _normalize_field(label)
    loc = page.locator(f'[ng-reflect-name="{attr}"]')
    if loc.count() > 0:
        loc.first.fill(value)
        return f"ng-reflect-name={attr}"
    try:
        page.get_by_label(label, exact=False).fill(value)
        return f"label={label}"
    except Exception:  # noqa: BLE001
        pass
    page.locator(f'label:has-text("{label}")').locator("xpath=..").locator(
        "input, textarea, select"
    ).first.fill(value)
    return f"label-near={label}"


def _normalize_field(label: str) -> str:
    key = re.sub(r"[^a-z0-9]+", "", label.strip().lower())
    mapping = {
        "firstname": "labelFirstName",
        "labelfirstname": "labelFirstName",
        "lastname": "labelLastName",
        "labellastname": "labelLastName",
        "companyname": "labelCompanyName",
        "labelcompanyname": "labelCompanyName",
        "roleincompany": "labelRole",
        "role": "labelRole",
        "labelrole": "labelRole",
        "labelroleincompany": "labelRole",
        "address": "labelAddress",
        "labeladdress": "labelAddress",
        "email": "labelEmail",
        "labelemail": "labelEmail",
        "phonenumber": "labelPhone",
        "phone": "labelPhone",
        "labelphone": "labelPhone",
        "labelphonenumber": "labelPhone",
    }
    return mapping.get(key, label)
