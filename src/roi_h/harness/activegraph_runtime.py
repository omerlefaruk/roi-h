"""ROI-H runtime type for ActiveGraph-backed modular automation runs."""

from activegraph import Runtime


class ROIHRuntime(Runtime):  # type: ignore[misc]
    """Named runtime boundary for ROI-H run loading and persistence."""


__all__ = ["ROIHRuntime"]
