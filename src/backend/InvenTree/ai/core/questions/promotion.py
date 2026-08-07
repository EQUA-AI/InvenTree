"""Option promotion: turning discarded server signals into question options.

Two sources (S22):

* ``search_manuals``' ambiguous machine resolution already returns
  RBAC-scoped ``machine_candidates`` that only the model ever saw — the
  best-prepared option source in the stack.
* The wf8 clarify path fires when capability selection scores nothing; the
  recoverable signals are the matched lexicon terms (category terms exist,
  machine terms are new in ``capabilities.matched_machine_terms``).

Options are server-derived by construction (invariant 2): every ``ref`` is a
value the server itself resolved under the acting user's authorization.

The ContextVars below are the tool→workflow side channel (the
``bind_capability_run`` idiom): a tool proposes a question mid-run; the
workflow consumes it after ``agent.run`` returns and emits the deterministic
question instead of the model's free text. Refs travel only through the
proposal — never through the SSE event.
"""

from __future__ import annotations

import re
from contextvars import ContextVar

from ai.core.questions.schema import MAX_OPTIONS_TEXT, MAX_OPTIONS_VOICE

#: One proposal per run; set by a tool, consumed exactly once by the workflow.
pending_question_proposal: ContextVar[dict | None] = ContextVar(
    "aimms_pending_question_proposal", default=None
)


def set_question_proposal(proposal: dict) -> None:
    """Propose a question for this run (last writer wins — single slot)."""
    pending_question_proposal.set(proposal)


def consume_question_proposal() -> dict | None:
    """Get-and-clear the run's question proposal."""
    proposal = pending_question_proposal.get()
    pending_question_proposal.set(None)
    return proposal


def _slugify(term: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", term.casefold()).strip("-")


def options_cap(modality: str) -> int:
    """Per-modality option ceiling (voice reads ordinals inside 700 chars)."""
    return MAX_OPTIONS_VOICE if modality == "voice" else MAX_OPTIONS_TEXT


def promote_machine_candidates(candidates: list[dict], *, modality: str) -> list[dict]:
    """Options from the corpus search's ambiguous machine resolution.

    Input rows are ``{machine_id, name, serial}`` from the RBAC-scoped
    resolver, so the options inherit its scope. First candidate is
    recommended (resolver ranking); the tail past the cap is served by the
    host-rendered "Other" row.
    """
    options: list[dict] = []
    for index, candidate in enumerate(candidates[: options_cap(modality)]):
        machine_id = candidate.get("machine_id")
        name = str(candidate.get("name") or "").strip()
        serial = str(candidate.get("serial") or "").strip()
        if machine_id is None or not name:
            continue
        option = {
            "id": f"machine:{machine_id}",
            "label": f"{name} ({serial})" if serial else name,
            "kind": "machine",
            "ref": {"machine_id": machine_id, "serial": serial, "name": name},
        }
        if serial:
            option["description"] = f"Serial {serial}"
        if index == 0:
            option["recommended"] = True
        options.append(option)
    return options


def promote_lexicon_options(
    *,
    machine_terms: list[dict],
    category_terms: list[str],
    modality: str,
) -> list[dict]:
    """Options from matched lexicon terms on the clarify path.

    Machines first (more specific), then categories. Fewer than two options
    is not a question — the caller falls back to the free-text clarify agent.
    """
    options: list[dict] = []
    for descriptor in machine_terms:
        name = str(descriptor.get("name") or "").strip()
        if not name:
            continue
        options.append({
            "id": f"machine:{descriptor.get('machine_id')}",
            "label": name,
            "kind": "machine",
            "ref": {
                "machine_id": descriptor.get("machine_id"),
                "serial": str(descriptor.get("serial") or ""),
                "name": name,
            },
        })
    for term in category_terms:
        cleaned = str(term).strip()
        if not cleaned:
            continue
        options.append({
            "id": f"term:{_slugify(cleaned)}",
            "label": cleaned,
            "kind": "lexicon_term",
            "ref": {"term": cleaned},
        })
    options = options[: options_cap(modality)]
    if len(options) < 2:
        return []
    return options


__all__ = [
    "consume_question_proposal",
    "options_cap",
    "pending_question_proposal",
    "promote_lexicon_options",
    "promote_machine_candidates",
    "set_question_proposal",
]
