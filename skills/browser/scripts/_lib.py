"""Import helper so tool scripts can load _session regardless of loader module name."""
from __future__ import annotations
import sys
from pathlib import Path

def ensure() -> None:
    scripts = Path(__file__).resolve().parent
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
