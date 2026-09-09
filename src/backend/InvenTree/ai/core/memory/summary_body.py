"""The compaction summary body: per-fact objects, rendering, exclusions (M2 PR 3).

Plan of record §5.4 (kind registry, ``thread_summary``), §5.6 (summary
fan-out), §8.7 (the per-fact object, ops, fan-out). Stored shape is
unchanged on the outside — ``label + "\\n" + json.dumps(body)`` — while the
four protected lists hold objects instead of strings (``body_version`` 2).

This module is stdlib-only and imports nothing but ``ai.core.memory.vocabulary``
(GR-35: no Django, no ``agent_framework``). It is the single reader of the
body shape outside ``aichat.tasks``: the context assembler renders through
``render_for_context`` so ids, lifecycle, fingerprints and JSON braces never
reach a prompt.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from ai.core.memory.vocabulary import (
    MEMORY_TYPE_BY_LIST,
    FactLifecycle,
    FactOrigin,
    FactVerification,
)

BODY_VERSION = 2
ITEM_LISTS = ("open_questions", "pending_proposals", "machine_facts", "corrections")
CITATION_LIST = "citation_keys"
ID_PREFIX_BY_LIST = {
    "machine_facts": "mf",
    "open_questions": "oq",
    "pending_proposals": "pp",
    "corrections": "co",
}
_ID_PREFIX_RE = re.compile(r"^\s*\[(?:mf|oq|pp|co)\d+\]\s*")
_ID_RE = re.compile(r"^(?:mf|oq|pp|co)(\d+)$")
RENDER_HEADINGS = (
    ("open_questions", "Open questions:"),
    ("pending_proposals", "Pending proposals:"),
    ("machine_facts", "Machine facts:"),
    ("corrections", "Corrections:"),
)

_ITEM_KEYS = (
    "id",
    "text",
    "verification",
    "lifecycle",
    "origin",
    "memory_type",
    "directive_flags",
    "created_seq",
    "superseded_by",
    "fingerprint",
)


# --------------------------------------------------------------------------- #
# Parsing and item accessors                                                   #
# --------------------------------------------------------------------------- #
def parse_summary(summary: str | None) -> tuple[str, dict]:
    """Split the stored string into ``(label, body)``; a bad body is ``{}``."""
    label, _, rest = (summary or "").partition("\n")
    label = label.strip()
    try:
        body = json.loads(rest)
    except (ValueError, TypeError):
        return label, {}
    return label, body if isinstance(body, dict) else {}


def is_legacy_item(item: Any) -> bool:
    """A pre-PR 3 protected item: a bare string."""
    return isinstance(item, str)


def item_text(item: Any) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        return str(item.get("text") or "")
    return ""


def item_id(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("id") or "")
    return ""


def item_lifecycle(item: Any) -> str:
    if isinstance(item, dict):
        return str(item.get("lifecycle") or FactLifecycle.ACTIVE)
    return str(FactLifecycle.ACTIVE)


def is_active(item: Any) -> bool:
    return item_lifecycle(item) == FactLifecycle.ACTIVE and item_text(item).strip() != ""


def _as_list(value: Any) -> list:
    return list(value) if isinstance(value, list) else []


def active_items(body: dict, field: str) -> list:
    """The items of ``field`` a reader may show: active, non-blank."""
    return [item for item in _as_list(body.get(field)) if is_active(item)]


# --------------------------------------------------------------------------- #
# Fingerprints                                                                 #
# --------------------------------------------------------------------------- #
def normalize_text(text: str) -> str:
    """Lowercase, punctuation to spaces, whitespace collapsed."""
    lowered = re.sub(r"[^\w\s]", " ", str(text).lower())
    return " ".join(lowered.split())


def fingerprint(text: str) -> str:
    """16 hex chars of sha256 over the normalized text (M2; §5.6 HMAC is M3a)."""
    return hashlib.sha256(normalize_text(text).encode("utf-8")).hexdigest()[:16]


def strip_id_prefix(text: str) -> str:
    """Drop one leading ``[mf3]``-style id a model may echo back."""
    return _ID_PREFIX_RE.sub("", str(text), count=1)


# --------------------------------------------------------------------------- #
# Items and bodies                                                             #
# --------------------------------------------------------------------------- #
def new_item(
    text: str,
    *,
    field: str,
    item_id: str,
    created_seq: int,
    memory_type: str | None = None,
) -> dict:
    """The full per-fact object (§8.7) for a compaction-produced text."""
    return {
        "id": str(item_id),
        "text": str(text),
        "verification": str(FactVerification.INFERRED),
        "lifecycle": str(FactLifecycle.ACTIVE),
        "origin": str(FactOrigin.COMPACTION),
        "memory_type": str(memory_type or MEMORY_TYPE_BY_LIST[field]),
        "directive_flags": [],
        "created_seq": int(created_seq),
        "superseded_by": None,
        "fingerprint": fingerprint(text),
    }


def _coerce_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def upgrade_item(
    item: str | dict, *, field: str, next_id: int, created_seq: int
) -> tuple[dict, int]:
    """Bring one item to the v2 object shape; returns ``(item, next_id)``.

    A string becomes a fresh object and consumes an id. A dict keeps its
    id, lifecycle, fingerprint and memory_type; missing keys are filled
    with the defaults; unknown keys are dropped.
    """
    prefix = ID_PREFIX_BY_LIST[field]
    if not isinstance(item, dict):
        text = strip_id_prefix(item_text(item)).strip()
        return new_item(text, field=field, item_id=f"{prefix}{next_id}", created_seq=created_seq), (
            next_id + 1
        )
    text = str(item.get("text") or "")
    identifier = str(item.get("id") or "")
    if not identifier:
        identifier = f"{prefix}{next_id}"
        next_id += 1
    flags = item.get("directive_flags")
    superseded_by = item.get("superseded_by")
    upgraded = {
        "id": identifier,
        "text": text,
        "verification": str(item.get("verification") or FactVerification.INFERRED),
        "lifecycle": str(item.get("lifecycle") or FactLifecycle.ACTIVE),
        "origin": str(item.get("origin") or FactOrigin.COMPACTION),
        "memory_type": str(item.get("memory_type") or MEMORY_TYPE_BY_LIST[field]),
        "directive_flags": [str(flag) for flag in flags] if isinstance(flags, list) else [],
        "created_seq": _coerce_int(item.get("created_seq"), created_seq),
        "superseded_by": str(superseded_by) if superseded_by else None,
        "fingerprint": str(item.get("fingerprint") or fingerprint(text)),
    }
    return upgraded, next_id


def _id_suffix(identifier: str) -> int:
    match = _ID_RE.match(identifier)
    return int(match.group(1)) if match else 0


def _next_id_for(body: dict) -> int:
    """The first id number that is free in ``body``.

    ``max(next_item_id, highest suffix present + 1)`` over every item of
    ``ITEM_LISTS``: the stored counter alone is not trusted, so a mixed or
    hand-edited body (objects beside legacy strings, a missing or corrupt
    counter) can never mint an id it already holds.
    """
    source = body if isinstance(body, dict) else {}
    highest = 0
    for field in ITEM_LISTS:
        for item in _as_list(source.get(field)):
            highest = max(highest, _id_suffix(item_id(item)))
    return max(_coerce_int(source.get("next_item_id"), 1) or 1, highest + 1)


def _well_formed_exclusion(entry: Any) -> dict | None:
    if not isinstance(entry, dict):
        return None
    fp = str(entry.get("fingerprint") or "")
    ident = str(entry.get("item_id") or "")
    if not fp and not ident:
        return None
    return {
        "fingerprint": fp,
        "item_id": ident,
        "created_seq": _coerce_int(entry.get("created_seq"), 0),
        "reason": str(entry.get("reason") or ""),
    }


def upgrade_body(body: dict, *, created_seq: int) -> dict:
    """A new v2 body carrying exactly the known keys; idempotent."""
    source = body if isinstance(body, dict) else {}
    next_id = _next_id_for(source)
    upgraded: dict[str, Any] = {"label": str(source.get("label") or "")}
    for field in ITEM_LISTS:
        items: list[dict] = []
        for item in _as_list(source.get(field)):
            if not isinstance(item, (str, dict)) or not item_text(item).strip():
                continue
            converted, next_id = upgrade_item(
                item, field=field, next_id=next_id, created_seq=created_seq
            )
            if converted["text"].strip():
                items.append(converted)
        upgraded[field] = items
    citations = [str(x) for x in _as_list(source.get(CITATION_LIST)) if str(x).strip()]
    upgraded[CITATION_LIST] = list(dict.fromkeys(citations))
    upgraded["narrative"] = str(source.get("narrative") or "")
    exclusions = (_well_formed_exclusion(e) for e in _as_list(source.get("exclusions")))
    upgraded["exclusions"] = [e for e in exclusions if e is not None]
    upgraded["next_item_id"] = next_id
    upgraded["body_version"] = BODY_VERSION
    return upgraded


# --------------------------------------------------------------------------- #
# Rendering                                                                    #
# --------------------------------------------------------------------------- #
def render_for_context(summary: str | None, max_chars: int | None = None) -> str:
    """Plain text for the prompt: label, headed ``- `` lines, citations, narrative.

    Active items only; never ids, lifecycle, fingerprints or JSON braces.
    An unparsable body renders as the label line alone.
    """
    label, body = parse_summary(summary)
    lines: list[str] = [label] if label else []
    for field, heading in RENDER_HEADINGS:
        texts = [" ".join(item_text(item).split()) for item in active_items(body, field)]
        if texts:
            lines.append(heading)
            lines.extend(f"- {text}" for text in texts)
    keys = [str(k).strip() for k in _as_list(body.get(CITATION_LIST)) if str(k).strip()]
    if keys:
        lines.append("Citations: " + ", ".join(keys))
    narrative = " ".join(str(body.get("narrative") or "").split())
    if narrative:
        lines.append(f"Narrative: {narrative}")
    text = "\n".join(lines)
    return text[:max_chars] if max_chars is not None else text


# --------------------------------------------------------------------------- #
# Exclusions (GR-03 non-revival)                                               #
# --------------------------------------------------------------------------- #
def excluded_keys(body: dict) -> tuple[frozenset[str], frozenset[str]]:
    """``(ids, fingerprints)`` named by ``body["exclusions"]``."""
    ids: set[str] = set()
    fingerprints: set[str] = set()
    for entry in _as_list(body.get("exclusions")):
        if not isinstance(entry, dict):
            continue
        if entry.get("item_id"):
            ids.add(str(entry["item_id"]))
        if entry.get("fingerprint"):
            fingerprints.add(str(entry["fingerprint"]))
    return frozenset(ids), frozenset(fingerprints)


def apply_exclusions(
    body: dict, *, reason_lifecycle: str = str(FactLifecycle.FORGOTTEN)
) -> tuple[dict, int]:
    """Flip every active item an exclusion names; returns ``(body, hits)``.

    Pure: the input is not mutated. A hit blanks the narrative (it may
    restate the forgotten text). A legacy string that is hit is minted into
    an object; its id comes from ``_next_id_for`` so it never collides with
    an id the body already holds, even when the body was not upgraded first.
    """
    ids, fingerprints = excluded_keys(body)
    result = dict(body)
    hits = 0
    if not ids and not fingerprints:
        return result, 0
    next_id = _next_id_for(body)
    minted = False
    for field in ITEM_LISTS:
        items: list = []
        for item in _as_list(body.get(field)):
            if is_active(item):
                fp = (
                    str(item.get("fingerprint") or "") if isinstance(item, dict) else ""
                ) or fingerprint(item_text(item))
                if item_id(item) in ids or fp in fingerprints:
                    if isinstance(item, dict):
                        flipped = dict(item)
                    else:
                        flipped, next_id = upgrade_item(
                            item, field=field, next_id=next_id, created_seq=0
                        )
                        minted = True
                    flipped["lifecycle"] = str(reason_lifecycle)
                    items.append(flipped)
                    hits += 1
                    continue
            items.append(item)
        result[field] = items
    if hits:
        result["narrative"] = ""
    if minted:
        result["next_item_id"] = next_id
    return result, hits


__all__ = [
    "BODY_VERSION",
    "CITATION_LIST",
    "ID_PREFIX_BY_LIST",
    "ITEM_LISTS",
    "RENDER_HEADINGS",
    "active_items",
    "apply_exclusions",
    "excluded_keys",
    "fingerprint",
    "is_active",
    "is_legacy_item",
    "item_id",
    "item_lifecycle",
    "item_text",
    "new_item",
    "normalize_text",
    "parse_summary",
    "render_for_context",
    "strip_id_prefix",
    "upgrade_body",
    "upgrade_item",
]
