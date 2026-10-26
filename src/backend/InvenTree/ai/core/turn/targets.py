"""Resolve a turn's typed record target without changing durable focus/scope."""

from __future__ import annotations

import re
from typing import Any


def machine_candidates(text: str) -> frozenset[int]:
    """Typed IDs only; a model number or the '2' in a machine name is not an ID."""
    values = re.findall(r"\bmachine\s+(?:id\s*)?[:#]?\s*(\d{1,7})\b", text, re.I)
    values += re.findall(
        r"\bmachine\b[^?\n]{0,180}?\(\s*(?:id\s*[:#]?\s*|#)(\d{1,7})\s*\)",
        text,
        re.I,
    )
    return frozenset(int(value) for value in values if int(value) > 0)


def identity_requested(text: str) -> bool:
    """A narrow identity lookup; history, condition and parts still use tools."""
    from ai.core.tools.capabilities import _write_intent

    return bool(
        re.search(r"\b(?:model|serial|identity|asset\s+(?:summary\s+)?card)\b", text, re.I)
        and not re.search(
            r"\b(?:work[ -]?orders?|history|trend|parts?|signals?|health|anomalies|manuals?|documents?)\b",
            text,
            re.I,
        )
        and _write_intent(text.casefold()) is None
    )


def read_identity(actor: Any, machine_id: int) -> dict | None:
    """Read the same identity projection as the native page, under current access."""
    from ai.core.focus.resolution import read_machine
    from assets.ai_read import machine_display_identity
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.filter(pk=actor.user_pk, is_active=True).first()
    machine = read_machine(user, machine_id) if user is not None else None
    return machine_display_identity(machine) if machine is not None else None


def identity_message(identity: dict) -> str:
    """Render record values literally, without interpreting them as Markdown."""

    def literal(value):
        value = str(value or "Unavailable")
        marker = "`" * (max((len(run) for run in re.findall(r"`+", value)), default=0) + 1)
        return f"{marker} {value} {marker}"

    return (
        f"Recorded machine {identity['machine_id']}: {literal(identity['name'])}. "
        f"Model: {literal(identity['model'])}. "
        f"Serial number: {literal(identity['serial'])}. "
        f"Recorded location: {literal(identity['location'])}. "
        f"Source: machine record {identity['machine_id']}."
    )


def resolve_target(actor: Any, text: str, focus: Any, work: Any, scope: Any) -> dict[str, Any]:
    """Candidates are freshly authorized; a typed scope can only narrow them."""
    from ai.core.focus.commands import is_bare_anaphora, selected_focus_kind
    from ai.core.focus.contract import FocusCandidate, FocusRejected, focus_from_stored
    from ai.core.focus.resolution import resolve_focus_candidate
    from django.contrib.auth import get_user_model

    ids = machine_candidates(text)
    if len(ids) > 1:
        return {"error": "Several machine IDs were supplied. Select one machine for this request."}
    candidate = next(iter(ids), None)
    if candidate is None and (selected_focus_kind(text) == "machine" or is_bare_anaphora(text)):
        if isinstance(work, dict) and type(work.get("machine_id")) is int:
            candidate = work["machine_id"]
        elif isinstance(focus, dict):
            target = focus_from_stored(focus.get("focus"))
            if target is not None:
                candidate = target.machine_id
    if candidate is None:
        return {}
    stored = (scope or {}).get("scope") or {}
    if stored.get("mode") == "explicit_assets" and candidate not in stored.get("machine_ids", []):
        return {
            "error": "The requested machine is outside this conversation's analysis scope. Change the scope before including it."
        }
    user = get_user_model().objects.filter(pk=actor.user_pk, is_active=True).first()
    try:
        target = resolve_focus_candidate(user, FocusCandidate(kind="machine", id=candidate))
    except FocusRejected:
        return {
            "error": "The requested machine is unavailable to you. No broader machine search was used."
        }
    return {"kind": "machine", "id": target.id, "label": target.label}
