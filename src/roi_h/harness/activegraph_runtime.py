"""ROI-H ActiveGraph runtime extensions required by the public CLI contract."""

from __future__ import annotations

from typing import Any

from activegraph import Event, Runtime
from activegraph.runtime.exec_errors import ApprovalNotFoundError


class ROIHRuntime(Runtime):  # type: ignore[misc]
    """ActiveGraph runtime with durable approval rejection."""

    def reject_approval(
        self,
        approval_id: str,
        *,
        rejected_by: str = "user",
        reason: str = "",
    ) -> dict[str, Any]:
        """Reject a deferred object without materializing it."""
        if self._pack_state is None:
            raise ApprovalNotFoundError(approval_id, pending_count=0)
        for index, pending in enumerate(self._pack_state.pending_approvals):
            if pending.id != approval_id:
                continue
            self._pack_state.pending_approvals.pop(index)
            self.graph.emit(
                Event(
                    id=self.graph.ids.event(),
                    type="approval.rejected",
                    payload={
                        "approval_id": approval_id,
                        "object_type": pending.object_type,
                        "rejected_by": rejected_by,
                        "reason": reason,
                    },
                    actor="runtime",
                    frame_id=self.frame.id if self.frame else None,
                    caused_by=None,
                    timestamp=self.graph.clock.now(),
                )
            )
            return {
                "approval_id": approval_id,
                "status": "denied",
                "rejected_by": rejected_by,
                "reason": reason,
            }
        raise ApprovalNotFoundError(
            approval_id,
            pending_count=len(self._pack_state.pending_approvals),
        )

    def restore_rejections(self) -> None:
        """Remove rejected approvals rebuilt by ActiveGraph 1.10 reload."""
        rejected = {
            str(event.payload.get("approval_id") or "")
            for event in self.graph.events
            if event.type == "approval.rejected"
        }
        if not rejected or self._pack_state is None:
            return
        self._pack_state.pending_approvals[:] = [
            item for item in self._pack_state.pending_approvals if item.id not in rejected
        ]


__all__ = ["ROIHRuntime"]
