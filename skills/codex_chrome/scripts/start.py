"""codex_chrome.start"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from _bridge import FakeBridge  # noqa: E402
from _contract import Output  # noqa: E402

TOOL_ID = "start"
DESCRIPTION = "Start or attach to one named Codex Chrome profile binding."
DETERMINISTIC = False
REQUIRES_APPROVAL = False
TOOL_EFFECT = "write"
IDEMPOTENCY = "key"
ALLOW_IN_PROD = True
TIMEOUT_SECONDS = 30.0
SECRET_NAMES = ()
NETWORK_HOSTS = ()
FILESYSTEM_ROOTS = ()


class Input(BaseModel):
    """Start input."""

    profile_binding: str = Field(
        default="default",
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,63}$",
    )
    mode: Literal["start", "attach"] = "start"


def run(args: Input) -> Output:
    """Claim the selected fake provider binding."""
    return Output.model_validate(FakeBridge().start(args.profile_binding, args.mode))
