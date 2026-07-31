"""Global test isolation."""

# ruff: noqa: INP001

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPOSITORY = Path(__file__).resolve().parents[1]
if str(_REPOSITORY) not in sys.path:
    sys.path.insert(0, str(_REPOSITORY))


@pytest.fixture(autouse=True)
def _use_isolated_browser_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the normal suite deterministic and free of persistent browser processes."""
    monkeypatch.setenv("ROI_H_BROWSER", "stub")
