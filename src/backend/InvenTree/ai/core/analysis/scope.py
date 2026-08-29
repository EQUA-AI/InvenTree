"""The active analysis scope contract (S1, Workstream A).

The analysis scope is a durable, server-owned *narrowing* of what analysis
answers may draw on. It is never authorization: the authenticated
principal's boundary (``ChatThread.scope_key``/``scope_hash`` and the
per-record scope resolvers) stays authoritative, and every machine id here
is a candidate until the server re-authorizes it — on the update and again
on every turn.

Modes:

- ``all_authorized_assets`` — every asset the principal is currently
  authorized for (displayed "Authorized fleet"). The requestable default.
- ``explicit_assets`` — a bounded, server-authorized machine-id subset.
- ``legacy_unconfirmed`` — the read-side state of every thread that predates
  typed scope (empty stored payload, version 0). It cannot be requested and
  is never silently converted; maintenance-analysis intents require the user
  to confirm a real scope first.
- ``site_group`` — RESERVED for the multi-site upgrade. Requesting it is a
  typed rejection today; nothing may ever fall through it open.

Selected work orders / documents (conversational focus) and transient query
filters are deliberately NOT stored here — the scope holds only the durable
asset selection, the optional date window, and the source-class allowlist.

Stdlib-only: this module is imported by the Django plane
(``aichat.services.threads``) and the AI plane alike.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import logging
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1

MODE_ALL_AUTHORIZED = "all_authorized_assets"
MODE_EXPLICIT = "explicit_assets"
MODE_LEGACY = "legacy_unconfirmed"
#: Reserved for the multi-site upgrade (decision record Q1). Always rejected.
MODE_SITE_GROUP = "site_group"

#: Modes a client may request. ``legacy_unconfirmed`` is read-side only.
REQUESTABLE_MODES = frozenset({MODE_ALL_AUTHORIZED, MODE_EXPLICIT})

#: Ordered mode list for the generated wire contract. A frozenset has no
#: stable iteration order and the generator is byte-deterministic, so the
#: wire union is emitted from this tuple (invariant-pinned in
#: ``test_scope_wire.py`` against ``REQUESTABLE_MODES`` + read-side modes).
WIRE_MODES: tuple[str, ...] = (
    MODE_ALL_AUTHORIZED,
    MODE_EXPLICIT,
    MODE_LEGACY,
    MODE_SITE_GROUP,
)

#: Source classes an analysis scope may narrow to (all by default).
SOURCE_CLASSES = (
    "controlled_document",
    "asset_attachment",
    "work_order",
    "maintenance_record",
)

#: Decision record Q9: explicit selections are bounded; larger analyses use
#: the authorized-fleet mode.
MAX_EXPLICIT_MACHINES = 50

MAX_DISPLAY_LABEL = 120


class ScopeValidationError(ValueError):
    """A malformed or unsupported scope request. Safe to echo to the client."""


class SiteGroupUnavailable(ScopeValidationError):
    """The reserved multi-site mode was requested before the upgrade exists."""

    def __init__(self) -> None:
        super().__init__("site_group scope is not available")


class ScopeRejected(Exception):
    """An authorization intersection failed.

    Deliberately generic: the message never discloses which candidate id
    failed or whether it exists (decision record Q6 — reject the entire
    update, preserve the previous scope).
    """

    def __init__(self) -> None:
        super().__init__("Scope update rejected")


@dataclass(frozen=True, slots=True)
class AnalysisScope:
    """One normalized, immutable analysis scope."""

    mode: str
    machine_ids: tuple[int, ...] = ()
    date_from: str | None = None
    date_to: str | None = None
    source_classes: tuple[str, ...] = field(default=SOURCE_CLASSES)
    display_label: str = ""


def legacy_scope() -> AnalysisScope:
    """The read-side scope of a thread that predates typed scope."""
    return AnalysisScope(mode=MODE_LEGACY)


def _validate_date(value: object, *, field_name: str) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ScopeValidationError(f"{field_name} must be an ISO date string")
    try:
        datetime.date.fromisoformat(value)
    except ValueError as exc:
        raise ScopeValidationError(f"{field_name} must be an ISO date (YYYY-MM-DD)") from exc
    return value


def _validate_machine_ids(raw: object) -> tuple[int, ...]:
    if raw is None:
        return ()
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Iterable):
        raise ScopeValidationError("machine_ids must be a list of integers")
    ids: set[int] = set()
    for item in raw:
        if isinstance(item, bool) or not isinstance(item, int) or item <= 0:
            raise ScopeValidationError("machine_ids must be positive integers")
        ids.add(item)
    if len(ids) > MAX_EXPLICIT_MACHINES:
        raise ScopeValidationError(
            f"explicit_assets accepts at most {MAX_EXPLICIT_MACHINES} machines"
        )
    return tuple(sorted(ids))


def _validate_source_classes(raw: object) -> tuple[str, ...]:
    if raw is None:
        return SOURCE_CLASSES
    if isinstance(raw, (str, bytes)) or not isinstance(raw, Iterable):
        raise ScopeValidationError("source_classes must be a list")
    selected = []
    for item in raw:
        if item not in SOURCE_CLASSES:
            raise ScopeValidationError("Unknown source class")
        if item not in selected:
            selected.append(item)
    if not selected:
        raise ScopeValidationError("source_classes must not be empty")
    # Canonical order is the declaration order, not request order.
    return tuple(cls for cls in SOURCE_CLASSES if cls in selected)


def normalize_scope_request(payload: Mapping[str, object]) -> AnalysisScope:
    """Validate one client scope request into a normalized ``AnalysisScope``.

    Raises ``ScopeValidationError`` (client-safe message) on malformed input
    and ``SiteGroupUnavailable`` for the reserved multi-site mode.
    """
    if not isinstance(payload, Mapping):
        raise ScopeValidationError("scope must be an object")

    mode = payload.get("mode")
    if mode == MODE_SITE_GROUP:
        raise SiteGroupUnavailable()
    if mode not in REQUESTABLE_MODES:
        raise ScopeValidationError("Unknown scope mode")

    machine_ids = _validate_machine_ids(payload.get("machine_ids"))
    if mode == MODE_EXPLICIT and not machine_ids:
        raise ScopeValidationError("explicit_assets requires machine_ids")
    if mode == MODE_ALL_AUTHORIZED and machine_ids:
        raise ScopeValidationError("all_authorized_assets does not accept machine_ids")

    window = payload.get("date_window") or {}
    if not isinstance(window, Mapping):
        raise ScopeValidationError("date_window must be an object")
    date_from = _validate_date(window.get("from"), field_name="date_window.from")
    date_to = _validate_date(window.get("to"), field_name="date_window.to")
    if date_from is not None and date_to is not None and date_from >= date_to:
        # Half-open [from, to) — an empty or inverted window is a mistake.
        raise ScopeValidationError("date_window must satisfy from < to")

    label = payload.get("display_label") or ""
    if not isinstance(label, str) or len(label) > MAX_DISPLAY_LABEL:
        raise ScopeValidationError("display_label is invalid")

    return AnalysisScope(
        mode=str(mode),
        machine_ids=machine_ids,
        date_from=date_from,
        date_to=date_to,
        source_classes=_validate_source_classes(payload.get("source_classes")),
        display_label=label,
    )


def scope_to_payload(scope: AnalysisScope) -> dict[str, object]:
    """The stored/wire JSON shape of a normalized scope."""
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": scope.mode,
        "machine_ids": list(scope.machine_ids),
        "date_window": {"from": scope.date_from, "to": scope.date_to},
        "source_classes": list(scope.source_classes),
        "display_label": scope.display_label,
    }


def scope_from_stored(payload: object) -> AnalysisScope:
    """Read a stored scope payload back into a normalized scope.

    An empty payload is the legacy-unconfirmed state. A malformed or
    unknown-version payload also degrades to ``legacy_unconfirmed`` — the
    most restrictive state (analysis requires re-confirmation), so
    corruption fails closed rather than widening anything.
    """
    if not isinstance(payload, Mapping) or not payload:
        return legacy_scope()
    if payload.get("schema_version") != SCHEMA_VERSION:
        logger.warning("analysis scope: unknown schema_version; treating as unconfirmed")
        return legacy_scope()
    try:
        return normalize_scope_request(payload)
    except ScopeValidationError:
        logger.warning("analysis scope: stored payload malformed; treating as unconfirmed")
        return legacy_scope()


def canonical_scope_json(scope: AnalysisScope) -> str:
    """The canonical (sorted, compact) JSON used for hashing."""
    return json.dumps(
        scope_to_payload(scope), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    )


def scope_hash(scope: AnalysisScope) -> str:
    """SHA-256 over the canonical scope JSON."""
    return hashlib.sha256(canonical_scope_json(scope).encode("utf-8")).hexdigest()


def require_all_authorized(
    machine_ids: Iterable[int], authorize: Callable[[int], bool]
) -> tuple[int, ...]:
    """Authorize every candidate id or reject the entire selection.

    ``authorize`` returns True only for a machine the acting principal may
    read *right now*. One failure rejects the whole update generically
    (``ScopeRejected``) — the caller must preserve the previous scope and
    must not disclose which id failed.
    """
    candidates = tuple(machine_ids)
    for machine_id in candidates:
        if not authorize(machine_id):
            raise ScopeRejected()
    return candidates


def display_summary(scope: AnalysisScope) -> str:
    """A short human label for the scope banner."""
    if scope.mode == MODE_LEGACY:
        return "Scope unconfirmed"
    if scope.mode == MODE_ALL_AUTHORIZED:
        return "Authorized fleet"
    if scope.display_label:
        return scope.display_label
    count = len(scope.machine_ids)
    return f"{count} selected asset" + ("" if count == 1 else "s")


__all__ = [
    "MAX_DISPLAY_LABEL",
    "MAX_EXPLICIT_MACHINES",
    "MODE_ALL_AUTHORIZED",
    "MODE_EXPLICIT",
    "MODE_LEGACY",
    "MODE_SITE_GROUP",
    "REQUESTABLE_MODES",
    "SCHEMA_VERSION",
    "SOURCE_CLASSES",
    "WIRE_MODES",
    "AnalysisScope",
    "ScopeRejected",
    "ScopeValidationError",
    "SiteGroupUnavailable",
    "canonical_scope_json",
    "display_summary",
    "legacy_scope",
    "normalize_scope_request",
    "require_all_authorized",
    "scope_from_stored",
    "scope_hash",
    "scope_to_payload",
]
