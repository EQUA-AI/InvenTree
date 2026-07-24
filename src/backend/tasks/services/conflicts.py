"""Scheduling conflict detection (S6, plan §5.5/§5.11).

Surfaces overlaps that a human should see but the system does not forbid:

* two work orders scheduled on the *same machine* at overlapping times — a
  machine can only do one job at once;
* two work orders on the *same assignee* at overlapping times — a technician
  cannot be in two places.

Overlap is wall-clock, not working-time: two jobs physically clash if their
scheduled windows intersect, regardless of shift hours. Only cards with a full
``[scheduled_start, scheduled_end]`` window are considered; a card without both
endpoints has no interval to clash. Conflicts are warnings, never hard blocks —
the schedule window returns them so the board/timeline can badge them.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any


def _overlaps(a_start, a_end, b_start, b_end) -> bool:
    """Return whether two half-open intervals intersect."""
    return a_start < b_end and b_start < a_end


def _pairwise_overlaps(cards, *, code: str, group_label: str, group_value):
    """Yield warning dicts for every overlapping pair in one resource group.

    ``cards`` are pre-sorted by start, so once a later card starts at or after
    the current card's end, no further card can overlap the current one.
    """
    warnings = []
    for index, first in enumerate(cards):
        for second in cards[index + 1 :]:
            if second.scheduled_start >= first.scheduled_end:
                break
            if _overlaps(
                first.scheduled_start,
                first.scheduled_end,
                second.scheduled_start,
                second.scheduled_end,
            ):
                warnings.append({
                    'code': code,
                    group_label: group_value,
                    'card_ids': sorted([first.pk, second.pk]),
                    'message': (
                        f'Work orders {first.pk} and {second.pk} overlap on the '
                        f'same {group_label.replace("_id", "").replace("_", " ")}.'
                    ),
                })
    return warnings


def detect_conflicts(cards, *, include_assignee: bool = True) -> list[dict[str, Any]]:
    """Return machine (and optionally assignee) overlap warnings for ``cards``."""
    scheduled = [
        card
        for card in cards
        if card.scheduled_start is not None and card.scheduled_end is not None
    ]

    warnings: list[dict[str, Any]] = []

    by_machine: dict[int, list] = defaultdict(list)
    for card in scheduled:
        if card.machine_id:
            by_machine[card.machine_id].append(card)
    for machine_id, group in by_machine.items():
        group.sort(key=lambda c: c.scheduled_start)
        warnings.extend(
            _pairwise_overlaps(
                group,
                code='machine_overlap',
                group_label='machine_id',
                group_value=machine_id,
            )
        )

    if include_assignee:
        by_assignee: dict[int, list] = defaultdict(list)
        for card in scheduled:
            if card.assigned_to_id:
                by_assignee[card.assigned_to_id].append(card)
        for assignee_id, group in by_assignee.items():
            group.sort(key=lambda c: c.scheduled_start)
            warnings.extend(
                _pairwise_overlaps(
                    group,
                    code='assignee_overlap',
                    group_label='assigned_to_id',
                    group_value=assignee_id,
                )
            )

    return warnings
