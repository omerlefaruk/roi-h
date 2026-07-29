from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

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
        session, "_read_state", lambda: {"endpoint": "http://127.0.0.1:1", "pid": 42}
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
