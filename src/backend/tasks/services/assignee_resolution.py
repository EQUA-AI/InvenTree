"""Best-effort resolution of free-text assignee strings to ``User`` records.

``WorkOrder`` carries both a free-text ``assignee`` and an ``assigned_to`` FK.
Scheduling needs a real identity -- per-assignee overlap detection and "group by
assignee" are wrong against free text, because two spellings of one person read
as two people. S3b makes ``assigned_to`` authoritative by back-filling it from
``assignee``.

Matching is deliberately conservative. A wrong match is invisible once the code
starts trusting the FK, so every stage accepts only an *unambiguous* result and
anything else is reported rather than guessed. The report is the point: an
operator resolves the leftovers by hand, against real names, not a heuristic.

This module takes plain user-like objects (anything exposing ``pk``,
``username``, ``first_name``, ``last_name``) so it can run against a migration's
historical model as easily as the live one, and be unit-tested without the ORM.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ResolutionReport:
    """Outcome of resolving a set of assignee strings."""

    #: assignee string -> resolved user pk
    matched: dict[str, int] = field(default_factory=dict)
    #: names that matched nothing
    unmatched: list[str] = field(default_factory=list)
    #: name -> candidate pks, where more than one user matched at some stage
    ambiguous: dict[str, list[int]] = field(default_factory=dict)

    def as_log_lines(self) -> list[str]:
        """Human-readable summary for the migration to emit."""
        lines = [
            f'assignee resolution: {len(self.matched)} matched, '
            f'{len(self.unmatched)} unmatched, {len(self.ambiguous)} ambiguous'
        ]
        for name in sorted(self.unmatched):
            lines.append(
                f'  UNMATCHED: {name!r} -> left as free text, assigned_to null'
            )
        for name, pks in sorted(self.ambiguous.items()):
            lines.append(f'  AMBIGUOUS: {name!r} -> candidates {pks}, not assigned')
        return lines


def _normalize(value: str) -> str:
    return ' '.join(value.strip().split()).casefold()


def resolve_assignee(name: str, users: Iterable[Any]) -> int | list[int] | None:
    """Resolve one assignee string against ``users``.

    Returns the matched user's pk, ``None`` if nothing matched, or a list of
    candidate pks if a stage was ambiguous (more than one match).

    Stages, tried in order and each requiring a unique hit:

    1. exact username (case-sensitive)
    2. case-insensitive username
    3. case-insensitive full name (``"first last"``)

    A later stage is only consulted when the earlier ones found nothing, so an
    exact username always wins over a coincidental full-name match.
    """
    cleaned = name.strip()

    if not cleaned:
        return None

    user_list = list(users)
    normalized = _normalize(cleaned)

    # Stage 1: exact username.
    exact = [u for u in user_list if u.username == cleaned]
    if len(exact) == 1:
        return exact[0].pk
    if len(exact) > 1:
        return [u.pk for u in exact]

    # Stage 2: case-insensitive username.
    ci_username = [u for u in user_list if u.username.casefold() == normalized]
    if len(ci_username) == 1:
        return ci_username[0].pk
    if len(ci_username) > 1:
        return [u.pk for u in ci_username]

    # Stage 3: case-insensitive full name.
    full_name = [
        u
        for u in user_list
        if _normalize(f'{u.first_name} {u.last_name}') == normalized
        and (u.first_name or u.last_name)
    ]
    if len(full_name) == 1:
        return full_name[0].pk
    if len(full_name) > 1:
        return [u.pk for u in full_name]

    return None


def resolve_assignees(names: Iterable[str], users: Iterable[Any]) -> ResolutionReport:
    """Resolve many assignee strings, collecting a report.

    ``users`` is materialized once so the caller can pass a queryset without it
    being re-hit per name.
    """
    user_list = list(users)
    report = ResolutionReport()

    seen: dict[str, None] = {}
    for raw in names:
        if raw is None:
            continue
        name = raw.strip()
        if not name or name in seen:
            continue
        seen[name] = None

        result = resolve_assignee(name, user_list)

        if result is None:
            report.unmatched.append(name)
        elif isinstance(result, list):
            report.ambiguous[name] = result
        else:
            report.matched[name] = result

    return report


def build_report(matched, unmatched, ambiguous) -> ResolutionReport:
    """Convenience constructor used in tests."""
    return ResolutionReport(
        matched=dict(matched),
        unmatched=list(unmatched),
        ambiguous=defaultdict(list, ambiguous),
    )
