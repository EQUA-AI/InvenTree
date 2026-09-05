"""Conversation-history budgeting for the normalized turn pipeline (S47).

Moved verbatim from ``ai.core.turn_service``.
"""

from __future__ import annotations

_HISTORY_TRUNCATION_MARKER = "… [truncated]"
# A follow-up resolves against the immediately preceding exchange; dropping
# it for budget would silently change what "the second one" means.
_HISTORY_PROTECTED_NEWEST = 2
# M1 PR H (GR-33): on a total-budget hit drop the oldest FOUR messages at
# once, never one per turn — a one-message slide re-cuts the cached prefix
# every turn, a block cut keeps it stable until the next block.
_HISTORY_BLOCK = 4


def _budgeted_history(
    messages: list[dict[str, str]],
    *,
    max_message_chars: int,
    max_total_chars: int,
    reserved_chars: int = 0,
) -> list[dict[str, str]]:
    """Apply the S24 replay budgets to an oldest-first transcript.

    M1 PR H: whole messages are dropped in blocks of four (GR-33).

    Two independent caps, each disabled at 0: a message keeps its head up to
    ``max_message_chars`` with a visible truncation marker, then whole
    messages are dropped oldest-first until the transcript fits
    ``max_total_chars``. The newest two messages are never dropped, even
    when they alone exceed the total budget.

    ``reserved_chars`` (S38) pre-charges the budget for content the caller
    will prepend AFTER budgeting (the compaction summary note). Without the
    reservation a prepended note would be at index 0 — the first thing the
    drop loop removes.
    """
    budgeted: list[dict[str, str]] = []
    for message in messages:
        content = str(message.get("content", ""))
        if 0 < max_message_chars < len(content):
            content = content[:max_message_chars].rstrip() + _HISTORY_TRUNCATION_MARKER
        budgeted.append({**message, "content": content})
    if max_total_chars <= 0:
        return budgeted
    effective_total = max(0, max_total_chars - max(0, reserved_chars))
    total = sum(len(entry["content"]) for entry in budgeted)
    while total > effective_total and len(budgeted) > _HISTORY_PROTECTED_NEWEST:
        # Whole-block cut, bounded by the protected tail (GR-33).
        block = min(_HISTORY_BLOCK, len(budgeted) - _HISTORY_PROTECTED_NEWEST)
        for _ in range(block):
            total -= len(budgeted.pop(0)["content"])
    return budgeted
