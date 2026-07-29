"""Run the ROI-H observer directly."""

from __future__ import annotations

import argparse

from roi_h.harness.workspace import resolve_home
from roi_h.observer.server import serve_observer


def main() -> None:
    """Start the local read-only observer."""
    parser = argparse.ArgumentParser(description="ROI-H read-only run observer")
    parser.add_argument("--home", default=None)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true")
    args = parser.parse_args()
    home = resolve_home(args.home)
    serve_observer(home, port=args.port, open_browser=not args.no_open)


if __name__ == "__main__":
    main()
