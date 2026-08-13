"""The consumed-pending-question resolution record (S22, promoted in S47).

Moved verbatim from ``ai.core.turn_service._QuestionResolution``; the facade
re-exports it under the old private name for the existing import contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class QuestionResolution:
    """One consumed pending question plus the parser's verdict (S22).

    Everything the accept branch acts on comes from the PERSISTED record —
    the reply only ever selects; it can never supply values.
    """

    record: dict[str, Any]
    interpretation: Any

    @property
    def outcome(self) -> str:
        return str(self.interpretation.outcome)

    def _selected_option(self) -> dict[str, Any]:
        return dict(self.record["options"][self.interpretation.option_index])

    @property
    def routing_content(self) -> str:
        """The original intent enriched by the selected label, both persisted."""
        origin = str((self.record.get("origin") or {}).get("content") or "").strip()
        label = str(self._selected_option().get("label") or "").strip()
        if origin and label:
            return f"{origin} — {label}"
        return label or origin

    def context_payload(self) -> dict[str, Any]:
        """Trusted workflow context for an accepted selection (ref included)."""
        return {
            "interrupt_id": self.record.get("interrupt_id"),
            "source": self.record.get("source"),
            "option": self._selected_option(),
        }

    def unmatched_payload(self) -> dict[str, Any]:
        """Trusted context for an unmatched reply (loop guard input, S22).

        The producers use this to refuse re-asking the question the user just
        failed to answer — without it, a near-miss reply re-armed an
        identical card indefinitely (observed live 2026-08-08).
        """
        return {
            "outcome": "unmatched",
            "interrupt_id": self.record.get("interrupt_id"),
            "source": self.record.get("source"),
            "option_ids": [
                str(option.get("id") or "") for option in (self.record.get("options") or [])
            ],
        }

    def audit_payload(self) -> dict[str, Any]:
        """The durable, ref-free resolution record for canonical/metadata."""
        payload: dict[str, Any] = {
            "interrupt_id": self.record.get("interrupt_id"),
            "outcome": self.outcome,
            "answer_policy_version": self.interpretation.policy_version,
        }
        if self.outcome == "selected":
            payload["selected_option_id"] = self.interpretation.option_id
            payload["matched_by"] = self.interpretation.matched_by
        return payload
