"""Conversation-focus wire-contract models (mirror, not authority).

These pydantic models mirror the live payload shapes built in
``aichat.services.threads`` (``_focus_payload``/``set_focus``) and
``ai.core.focus.contract`` (``focus_payload``) so
``manage.py generate_wire_contract`` can emit the TypeScript interfaces.
They validate nothing on the serving path: the serving authority stays in
``threads.py``/``focus.resolution``. Round-trip tests pin the mirrors to the
live endpoint payloads.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class ConversationFocusRef(BaseModel):
    """One client candidate reference ``{kind, id}`` — never a grant."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    id: int


class ConversationFocusPayload(BaseModel):
    """The canonical, server-resolved focus projection (no hashes/versions)."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    id: int
    label: str
    machine_id: int | None
    work_order_id: int | None


class ThreadFocusPayload(BaseModel):
    """``GET/PUT /threads/{id}/focus`` response: projection + own version.

    ``focus_unavailable`` is the READ-path re-authorization verdict: the
    stored target exists but the caller may no longer read the record
    (revoked/deleted), so its label is withheld and the client should ask
    for a new target. It is ``False`` on every write echo.
    """

    model_config = ConfigDict(extra="forbid")

    focus: ConversationFocusPayload | None
    focus_version: int
    focus_unavailable: bool = False


class ThreadFocusUpdateRequest(BaseModel):
    """The ``PUT /threads/{id}/focus`` body (mirror of the route model).

    ``focus: null`` clears; ``expected_focus_version`` is mandatory — the
    update is optimistic and a canonical no-op still checks it.
    """

    model_config = ConfigDict(extra="forbid")

    focus: ConversationFocusRef | None = None
    expected_focus_version: int


class FocusCandidateSearchResult(BaseModel):
    """``GET /focus/candidates`` response: one bounded authorized page.

    Every candidate is re-authorized BEFORE its canonical label is exposed;
    ``has_more`` reports real server-side truncation so a bounded page can
    never masquerade as a unique match.
    """

    model_config = ConfigDict(extra="forbid")

    candidates: list[ConversationFocusPayload]
    returned_count: int
    has_more: bool


#: Machine-readable ``detail`` values the focus rail can return (the
#: ``SCOPE_ERROR_CODES``/``SERVER_VOICE_ERROR_CODES`` precedent). Pinned to
#: the ``app.py`` literals by tests.
FOCUS_ERROR_CODES: tuple[str, ...] = (
    "focus_version_conflict",
    "focus_update_rejected",
    "focus_out_of_analysis_scope",
    "focus_decision_not_set_aside",
)


__all__ = [
    "FOCUS_ERROR_CODES",
    "ConversationFocusPayload",
    "ConversationFocusRef",
    "FocusCandidateSearchResult",
    "ThreadFocusPayload",
    "ThreadFocusUpdateRequest",
]
