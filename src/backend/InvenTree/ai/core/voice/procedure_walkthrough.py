"""B7 (S33): read-only voice step-through of applied guided procedures.

Design constraints, in order:

**Verbatim or silent.** The text spoken for a step is the stored
``step_snapshot`` title/instruction EXACTLY — the snapshot is the citation
anchor, and no model ever generates over procedure instructions. If a
snapshot has no instruction text, the walkthrough says so rather than
inventing one.

**Stateless per utterance.** The client holds only a step cursor; every
utterance re-reads the scoped execution rows, so a step completed from the
normal screen mid-walkthrough is honestly reflected, and there is no
server-side session state to leak or desync.

**Writes ride the existing rail.** "Complete" posts through
``tasks.services.procedure_execution.complete_step`` as the acting user with
the execution's own version and a deterministic idempotency key — the same
audited command every button in the UI uses. This module adds no authority.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_NEXT_RE = re.compile(r"^\s*(?:next|next step|go on|continue|skip ahead)\b", re.IGNORECASE)
_REPEAT_RE = re.compile(
    r"^\s*(?:repeat|say (?:that|it) again|again|what was that)\b", re.IGNORECASE
)
_PREVIOUS_RE = re.compile(r"^\s*(?:previous|go back|back)\b", re.IGNORECASE)
_COMPLETE_RE = re.compile(
    r"^\s*(?:complete|done|mark (?:it |this )?(?:done|complete)|finished?|step complete)\b",
    re.IGNORECASE,
)
_STOP_RE = re.compile(
    r"^\s*(?:stop|exit|quit|end (?:the )?(?:walkthrough|procedure)|cancel)\b", re.IGNORECASE
)

WALKTHROUGH_POLICY_VERSION = "procedure-walkthrough-v1"


@dataclass(frozen=True)
class WalkthroughReply:
    """One deterministic reply: what to speak, where the cursor is now."""

    action: str
    position: int
    total: int
    speak_text: str
    done: bool = False
    step_key: str | None = None
    completed: bool = False
    error: str | None = None


def interpret_walkthrough_command(content: str) -> str:
    """Classify one utterance; anything unmatched is 'unknown' (fail closed)."""
    text = str(content or "")
    if _STOP_RE.search(text):
        return "stop"
    if _COMPLETE_RE.search(text):
        return "complete"
    if _REPEAT_RE.search(text):
        return "repeat"
    if _PREVIOUS_RE.search(text):
        return "previous"
    if _NEXT_RE.search(text):
        return "next"
    return "unknown"


def _snapshot_text(execution: Any) -> str:
    """The verbatim speakable text for one step execution."""
    snapshot = execution.step_snapshot or {}
    title = str(snapshot.get("title") or "").strip()
    instruction = str(snapshot.get("instruction") or "").strip()
    if not title and not instruction:
        return "This step has no stored instruction text. Check the screen."
    if title and instruction:
        return f"{title}. {instruction}"
    return title or instruction


def _step_line(execution: Any, position: int, total: int) -> str:
    """Position framing + the verbatim snapshot text."""
    status = str(execution.status or "")
    prefix = f"Step {position + 1} of {total}"
    if status and status not in ("pending", "open"):
        prefix = f"{prefix}, already {status}"
    return f"{prefix}. {_snapshot_text(execution)}"


def _ordered_executions(actor: Any, work_order_id: int) -> list[Any]:
    """The scoped, ordered step executions for one work order.

    Scope-first through the same helpers the execution API uses; an
    out-of-scope work order surfaces as an empty walkthrough upstream of
    any snapshot text.
    """
    from tasks.models import WorkOrder
    from tasks.procedure_models import WorkOrderStepExecution
    from tasks.scope import require_work_order_scope

    work_order = WorkOrder.objects.get(pk=work_order_id)
    require_work_order_scope(actor, work_order)
    return list(
        WorkOrderStepExecution.objects.filter(application__work_order=work_order).order_by(
            "sequence", "step_key"
        )
    )


def walkthrough_reply(
    *,
    actor: Any,
    work_order_id: int,
    utterance: str,
    position: int,
) -> WalkthroughReply:
    """Advance one walkthrough turn. Synchronous; callers wrap for async."""
    from ai.core.config import get_settings

    if not get_settings().feature_guided_procedures:
        return WalkthroughReply(
            action="unavailable",
            position=0,
            total=0,
            speak_text="",
            done=True,
            error="FEATURE_DISABLED",
        )

    executions = _ordered_executions(actor, work_order_id)
    total = len(executions)
    if total == 0:
        return WalkthroughReply(
            action="empty",
            position=0,
            total=0,
            speak_text="No procedure is applied to this work order.",
            done=True,
        )

    position = max(0, min(int(position), total - 1))
    command = interpret_walkthrough_command(utterance)

    if command == "stop":
        return WalkthroughReply(
            action="stop",
            position=position,
            total=total,
            speak_text="Walkthrough ended.",
            done=True,
        )

    if command == "complete":
        execution = executions[position]
        try:
            from tasks.services.procedure_execution import complete_step

            updated = complete_step(
                work_order_id=work_order_id,
                application_id=execution.application_id,
                step_key=execution.step_key,
                actor=actor,
                expected_version=execution.version,
                idempotency_key=(
                    f"voice-procedure:{execution.application_id}:"
                    f"{execution.step_key}:{execution.version}"
                ),
            )
            completed = True
            outcome = f"Step {position + 1} marked {updated.status}."
        except Exception as exc:
            completed = False
            outcome = (
                "I could not complete that step: "
                f"{type(exc).__name__}. Use the screen to resolve it."
            )
        next_position = min(position + 1, total - 1)
        follow = (
            _step_line(executions[next_position], next_position, total)
            if completed and position + 1 < total
            else ""
        )
        return WalkthroughReply(
            action="complete",
            position=next_position if completed else position,
            total=total,
            speak_text=f"{outcome} {follow}".strip(),
            done=completed and position + 1 >= total,
            step_key=str(execution.step_key),
            completed=completed,
            error=None if completed else "COMPLETE_FAILED",
        )

    if command == "next":
        if position + 1 >= total:
            return WalkthroughReply(
                action="next",
                position=position,
                total=total,
                speak_text="That was the last step.",
                done=True,
            )
        position += 1
    elif command == "previous":
        position = max(0, position - 1)
    # repeat and unknown both re-read the current step; an unrecognized
    # utterance never advances the cursor or performs a write.

    execution = executions[position]
    return WalkthroughReply(
        action=command if command in ("next", "previous", "repeat") else "read",
        position=position,
        total=total,
        speak_text=_step_line(execution, position, total),
        step_key=str(execution.step_key),
    )


__all__ = [
    "WALKTHROUGH_POLICY_VERSION",
    "WalkthroughReply",
    "interpret_walkthrough_command",
    "walkthrough_reply",
]
