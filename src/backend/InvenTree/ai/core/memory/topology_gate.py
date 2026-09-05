"""The interim topology gate (M1 PR H; plan §9.4 Q73, §9.10 rider terms).

Until G4 publishes a graph, a turn that asks what supplies, isolates or
depends on a piece of equipment gets the deterministic
``TOPOLOGY_UNAVAILABLE`` sentence instead of model-authored topology prose.
The predicate is lexical and two-signal on purpose: a relation term from the
§9.10 rider list AND a reference to authorized equipment (a display name
token-matching the utterance, the same rule the clarify-first signal uses).
Either signal alone is not a topology question — a bare "what feeds this?"
without an identifiable machine routes normally and can ask.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ai.core.turn.request import _machine_name_matches

if TYPE_CHECKING:
    from collections.abc import Iterable

#: §9.10 rider terms (topology.read reachability), lower-cased phrases.
RELATION_TERMS: tuple[str, ...] = (
    "upstream",
    "downstream",
    "feeds",
    "fed by",
    "feed into",
    "supplies",
    "supplied by",
    "supply to",
    "isolate",
    "isolates",
    "isolated by",
    "isolation point",
    "breaker",
    "protects",
    "protected by",
    "depends on",
    "depend on",
    "dependent on",
    "alternate supply",
    "alternative supply",
)

_RELATION_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(term) for term in RELATION_TERMS) + r")\b", re.IGNORECASE
)


def has_relation_term(text: str) -> bool:
    """Whether the utterance names a topological relation."""
    return bool(_RELATION_RE.search(text or ""))


def references_equipment(text: str, equipment_names: Iterable[str]) -> bool:
    """Whether an authorized equipment name token-matches the utterance."""
    lowered = (text or "").lower()
    return any(name and _machine_name_matches(str(name), lowered) for name in equipment_names)


def is_topology_question(text: str, *, equipment_names: Iterable[str]) -> bool:
    """Both signals, or nothing: a relation term AND an equipment reference."""
    names = list(equipment_names or ())
    return has_relation_term(text) and references_equipment(text, names)


__all__ = ["RELATION_TERMS", "has_relation_term", "is_topology_question", "references_equipment"]
