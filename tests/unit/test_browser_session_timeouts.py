from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from roi_h.harness.logical_paths import PathScope, normalize_tool_output
from roi_h.harness.workspace import Workspace, create_project

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_cdp_connection_uses_a_bounded_timeout(monkeypatch) -> None:
    session = _load_module(
        "browser_session_timeout_test",
        REPO_ROOT / "skills/browser/scripts/_session.py",
    )
    calls: list[float | None] = []

    class FakeChromium:
        def connect_over_cdp(self, endpoint: str, *, timeout=None):
            calls.append(timeout)
            page = SimpleNamespace(set_default_timeout=lambda _value: None)
            context = SimpleNamespace(pages=[page])
            return SimpleNamespace(contexts=[context])

    fake_pw = SimpleNamespace(chromium=FakeChromium())
    monkeypatch.setattr(
        session,
        "_read_state",
        lambda: {"endpoint": "http://127.0.0.1:1", "pid": 42, "headed": False},
    )
    monkeypatch.setattr(session, "_pid_alive", lambda _pid: True)

    import playwright.sync_api

    monkeypatch.setattr(
        playwright.sync_api,
        "sync_playwright",
        lambda: SimpleNamespace(start=lambda: fake_pw),
    )

    browser = session.LiveBrowser()
    browser.connect()

    assert calls == [10_000]


@pytest.mark.parametrize(
    "initial_state",
    [
        None,
        "{not-json",
        "[]",
        '{"pid": 998}',
        '{"endpoint": "http://127.0.0.1:1", "pid": 999, "headed": false}',
    ],
    ids=["missing", "malformed", "wrong-shape", "incomplete", "stale-pid"],
)
def test_missing_or_invalid_state_starts_session_and_publishes_valid_json(
    monkeypatch,
    tmp_path: Path,
    initial_state: str | None,
) -> None:
    session = _load_module(
        f"browser_session_recovery_{initial_state!r}",
        REPO_ROOT / "skills/browser/scripts/_session.py",
    )
    state_path = tmp_path / "runtime" / "browser-session.json"
    monkeypatch.setenv("ROI_H_BROWSER_STATE", str(state_path))
    if initial_state is not None:
        state_path.parent.mkdir(parents=True)
        state_path.write_text(initial_state, encoding="utf-8")

    launches: list[tuple[int, bool]] = []
    monkeypatch.setattr(session, "_free_port", lambda: 9222)
    monkeypatch.setattr(session, "_pid_alive", lambda _pid: False)

    def launch(port: int, *, headed: bool) -> int:
        launches.append((port, headed))
        return 42

    monkeypatch.setattr(session, "_launch_chromium", launch)

    page = SimpleNamespace(set_default_timeout=lambda _value: None)
    context = SimpleNamespace(pages=[page])

    def connect_success(_endpoint: str, *, timeout: int):
        del timeout
        return SimpleNamespace(contexts=[context])

    chromium = SimpleNamespace(connect_over_cdp=connect_success)
    import playwright.sync_api

    monkeypatch.setattr(
        playwright.sync_api,
        "sync_playwright",
        lambda: SimpleNamespace(start=lambda: SimpleNamespace(chromium=chromium)),
    )

    browser = session.LiveBrowser()
    browser.connect(headed=False)

    assert launches == [(9222, False)]
    assert json.loads(state_path.read_text(encoding="utf-8")) == {
        "endpoint": "http://127.0.0.1:9222",
        "pid": 42,
        "headed": False,
        "ref_map": {},
        "last_url": "",
        "last_title": "",
    }
    assert list(state_path.parent.glob(f".{state_path.name}.*")) == []


def test_state_publish_failure_stops_fresh_process_and_preserves_error(
    monkeypatch,
    tmp_path: Path,
) -> None:
    session = _load_module(
        "browser_session_state_publish_failure_test",
        REPO_ROOT / "skills/browser/scripts/_session.py",
    )
    state_path = tmp_path / "runtime" / "browser-session.json"
    monkeypatch.setenv("ROI_H_BROWSER_STATE", str(state_path))
    alive = {42}
    killed: list[tuple[int, int]] = []
    publication_error = OSError("state publish denied")

    monkeypatch.setattr(session, "_free_port", lambda: 9222)

    def launch(_port: int, *, headed: bool) -> int:
        del headed
        return 42

    monkeypatch.setattr(session, "_launch_chromium", launch)
    monkeypatch.setattr(session, "_pid_alive", lambda pid: pid in alive)
    monkeypatch.setattr(session.time, "sleep", lambda _seconds: None)

    def kill(pid: int, signal: int) -> None:
        killed.append((pid, signal))
        alive.discard(pid)

    def fail_replace(_temporary: Path, target: Path) -> None:
        assert target == state_path
        raise publication_error

    monkeypatch.setattr(session.os, "kill", kill)
    monkeypatch.setattr(session.Path, "replace", fail_replace)

    with pytest.raises(OSError, match="state publish denied") as failure:
        session._start_session(headed=False)

    assert failure.value is publication_error
    assert killed == [(42, 15)]
    assert not state_path.exists()
    assert list(state_path.parent.glob(f".{state_path.name}.*")) == []


def test_fresh_launch_cdp_failure_restarts_once_and_recovers(monkeypatch, tmp_path: Path) -> None:
    session = _load_module(
        "browser_session_fresh_launch_retry_test",
        REPO_ROOT / "skills/browser/scripts/_session.py",
    )
    state_path = tmp_path / "runtime" / "browser-session.json"
    monkeypatch.setenv("ROI_H_BROWSER_STATE", str(state_path))
    ports = iter((9222, 9333))
    pids = iter((42, 43))
    alive: set[int] = set()
    launches: list[tuple[int, bool]] = []
    killed: list[tuple[int, int]] = []

    monkeypatch.setattr(session, "_free_port", lambda: next(ports))

    def launch(port: int, *, headed: bool) -> int:
        pid = next(pids)
        alive.add(pid)
        launches.append((port, headed))
        return pid

    def kill(pid: int, signal: int) -> None:
        killed.append((pid, signal))
        alive.discard(pid)

    monkeypatch.setattr(session, "_launch_chromium", launch)
    monkeypatch.setattr(session, "_pid_alive", lambda pid: pid in alive)
    monkeypatch.setattr(session.os, "kill", kill)
    monkeypatch.setattr(session.time, "sleep", lambda _seconds: None)

    endpoints: list[str] = []
    page = SimpleNamespace(set_default_timeout=lambda _value: None)
    context = SimpleNamespace(pages=[page])

    def connect(endpoint: str, *, timeout: int):
        del timeout
        endpoints.append(endpoint)
        if len(endpoints) == 1:
            message = "first CDP connection failed"
            raise ConnectionError(message)
        return SimpleNamespace(contexts=[context])

    import playwright.sync_api

    chromium = SimpleNamespace(connect_over_cdp=connect)
    monkeypatch.setattr(
        playwright.sync_api,
        "sync_playwright",
        lambda: SimpleNamespace(start=lambda: SimpleNamespace(chromium=chromium)),
    )

    browser = session.LiveBrowser()
    browser.connect(headed=False)

    assert launches == [(9222, False), (9333, False)]
    assert endpoints == ["http://127.0.0.1:9222", "http://127.0.0.1:9333"]
    assert killed == [(42, 15)]
    assert json.loads(state_path.read_text(encoding="utf-8"))["pid"] == 43


def test_two_session_failures_raise_stable_error_with_cause(monkeypatch, tmp_path: Path) -> None:
    session = _load_module(
        "browser_session_bounded_failure_test",
        REPO_ROOT / "skills/browser/scripts/_session.py",
    )
    state_path = tmp_path / "runtime" / "browser-session.json"
    monkeypatch.setenv("ROI_H_BROWSER_STATE", str(state_path))
    ports = iter((9222, 9333))
    pids = iter((42, 43))
    alive: set[int] = set()
    killed: list[int] = []
    monkeypatch.setattr(session, "_free_port", lambda: next(ports))

    def launch(_port: int, *, headed: bool) -> int:
        del headed
        pid = next(pids)
        alive.add(pid)
        return pid

    def kill(pid: int, _signal: int) -> None:
        killed.append(pid)
        alive.discard(pid)

    monkeypatch.setattr(session, "_launch_chromium", launch)
    monkeypatch.setattr(session, "_pid_alive", lambda pid: pid in alive)
    monkeypatch.setattr(session.os, "kill", kill)
    monkeypatch.setattr(session.time, "sleep", lambda _seconds: None)

    import playwright.sync_api

    chromium = SimpleNamespace(
        connect_over_cdp=lambda _endpoint, timeout: (_ for _ in ()).throw(
            ConnectionError(f"CDP failed after {timeout}ms")
        )
    )
    monkeypatch.setattr(
        playwright.sync_api,
        "sync_playwright",
        lambda: SimpleNamespace(start=lambda: SimpleNamespace(chromium=chromium)),
    )

    with pytest.raises(
        RuntimeError,
        match=r"browser\.session_unavailable: ConnectionError: CDP failed after 10000ms",
    ) as failure:
        session.LiveBrowser().connect(headed=False)

    assert isinstance(failure.value.__cause__, ConnectionError)
    assert killed == [42, 43]
    assert not state_path.exists()


def test_browser_state_is_scoped_to_the_active_project(monkeypatch, tmp_path: Path) -> None:
    session = _load_module(
        "browser_session_project_scope_test",
        REPO_ROOT / "skills/browser/scripts/_session.py",
    )
    monkeypatch.delenv("ROI_H_BROWSER_STATE", raising=False)
    monkeypatch.setenv("ROI_H_HOME", str(tmp_path / ".roi-h"))
    monkeypatch.setenv("ROI_H_PROJECT", "acme")

    assert (
        session.state_path() == (tmp_path / ".roi-h/projects/acme/browser-session.json").resolve()
    )
    assert session.profile_dir() == (tmp_path / ".roi-h/projects/acme/browser-profile").resolve()


def test_restored_page_selection_is_generic_and_prefers_newest_usable() -> None:
    session = _load_module(
        "browser_session_page_selection_test",
        REPO_ROOT / "skills/browser/scripts/_session.py",
    )
    pages = [
        SimpleNamespace(url="https://example.test/old"),
        SimpleNamespace(url="about:blank"),
        SimpleNamespace(url="chrome-error://chromewebdata/"),
        SimpleNamespace(url="https://example.test/current"),
    ]

    assert session._select_page(pages) is pages[-1]


def test_session_status_omits_private_state_path(
    monkeypatch,
    tmp_path: Path,
) -> None:
    session = _load_module(
        "browser_session_status_path_test",
        REPO_ROOT / "skills/browser/scripts/_session.py",
    )
    monkeypatch.setattr(
        session,
        "_read_state",
        lambda: {
            "endpoint": "http://127.0.0.1:9222",
            "pid": 42,
            "headed": False,
            "ref_map": {},
        },
    )
    monkeypatch.setattr(session, "_pid_alive", lambda _pid: True)
    status = session.session_status()

    assert "state_path" not in status

    home = tmp_path / ".roi-h"
    create_project(home, "demo", set_active=True)
    workspace = Workspace.open(home, project="demo", env="dev")
    normalized = normalize_tool_output(
        status,
        scope=PathScope(workspace, run_id="status-run"),
    )

    assert normalized == status


def test_windows_pid_probe_detects_current_process() -> None:
    session = _load_module(
        "browser_session_windows_pid_test",
        REPO_ROOT / "skills/browser/scripts/_session.py",
    )

    assert session._pid_alive(os.getpid()) is True
