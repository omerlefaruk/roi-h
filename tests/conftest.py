"""Global test isolation."""

# ruff: noqa: INP001

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _use_isolated_browser_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the normal suite deterministic and free of persistent browser processes."""
    monkeypatch.setenv("ROI_H_BROWSER", "stub")
