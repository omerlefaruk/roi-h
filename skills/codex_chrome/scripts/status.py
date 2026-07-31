"""codex_chrome.status"""

from __future__ import annotations

import sys
from pathlib import Path

from pydantic import BaseModel, Field

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from _bridge import FakeBridge  # noqa: E402
from _contract import Output  # noqa: E402

TOOL_ID = "status"
DESCRIPTION = "Read the run-owned Codex Chrome profile binding status."
DETERMINISTIC = True
REQUIRES_APPROVAL = False
TOOL_EFFECT = "read"
IDEMPOTENCY = "none"
ALLOW_IN_PROD = True
TIMEOUT_SECONDS = 30.0
SECRET_NAMES = ()
NETWORK_HOSTS = ()
FILESYSTEM_ROOTS = ()


class Input(BaseModel):
    """Status input."""

    profile_binding: str = Field(
        default="default",
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$",
    )


def run(args: Input) -> Output:
    """Read the selected fake provider binding."""
    return Output.model_validate(FakeBridge().status(args.profile_binding))
