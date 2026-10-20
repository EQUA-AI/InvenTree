"""Deterministic spoken focus selection, switching and clarification.

Explicit switches ("talk about machine 3", "switch to task 11") resolve
through the authorized readers ON THE APPLICATION TURN PATH — never a
realtime provider session, never a client-supplied label or grant. Exactly
one sufficiently identified candidate becomes confirmed focus; an ambiguous
match is offered through the existing persisted question mechanism with
server-owned option refs; an unmatched, expired or declined answer leaves
focus unchanged. A bare anaphor ("what is its status?") with NO confirmed
focus asks which target — it must never guess from a stale transcript
mention, the last page or a matching numeric id. A target switch sets aside
an unconfirmed pending decision through its ACTUAL coordinator, so a later
"yes" can never apply an old-target preview to the new focus.

Focus switches capture the turn (like a pending write confirmation does) and
close the one-turn question window; an already-confirmed/unknown write is
never replayed here — only an ARMED decision is set aside.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from ai.core.focus.contract import (
    KIND_KANBAN_CARD,
    KIND_MACHINE,
    KIND_WORK_ORDER,
    FocusOutOfAnalysisScope,
    FocusRejected,
    FocusSetAsideUnavailable,
    FocusValidationError,
    normalize_focus_request,
)

if TYPE_CHECKING:
    from ai.core.turn.state import TurnRun
    from ai.core.turn_service import NormalizedTurnService

#: The ``source`` stamp on question records this module arms/answers. Only a
#: selection from one of these records may ever update the durable focus.
FOCUS_QUESTION_SOURCE = "focus_selection"

_SWITCH_RE = re.compile(
    r"^\s*(?:hey\s+|please\s+|let(?:'s|’s)\s+|can\s+we\s+|ok(?:ay)?[\s,.]+)*"  # noqa: RUF001 - accept both apostrophe forms.
    r"(?:(?:talk|speak|chat)\s+about|tell\s+me\s+about|switch\s+to|focus\s+on|"
    r"get\s+back\s+to|go\s+back\s+to|change\s+(?:the\s+)?subject\s+to)\s+"
    r"(?P<target>.+?)\s*[.?!]*\s*$",
    re.IGNORECASE,
)

_KIND_PREFIX_RES = (
    (re.compile(r"^\s*(?:the\s+|a\s+)?(?:machine|asset|equipment)\b\s*", re.I), KIND_MACHINE),
    (re.compile(r"^\s*(?:the\s+|a\s+)?(?:task|card)\b\s*", re.I), KIND_KANBAN_CARD),
    (
        re.compile(r"^\s*(?:the\s+|a\s+)?(?:work\s*order|workorder|job|order)\b\s*", re.I),
        KIND_WORK_ORDER,
    ),
)

#: Bare anaphora: short information requests with a pronoun and NO explicit
#: entity marker. "that sounds good" is not one; "what is its status?" is.
_ANAPHORA_PRONOUN_RE = re.compile(
    r"\b(?:it|its|it's|this|that|these|those|they|them|their|he|she)\b", re.I
)
_ANAPHORA_REQUEST_RE = re.compile(
    r"^\s*(?:what|how|when|where|who|why|is|are|was|were|does|did|can|could|tell\s+me|"
    r"and)\b|\bstatus\b|\babout\b",
    re.I,
)
_MAX_ANAPHORA_TOKENS = 12

#: Pronoun/filler tokens that make a kind-less switch target an anaphora
#: rather than a name reference ("tell me about it" -> clarify/continue).
_PRONOUN_TOKENS = frozenset({
    "it",
    "its",
    "it's",
    "this",
    "that",
    "these",
    "those",
    "they",
    "them",
    "their",
    "one",
    "ones",
    "he",
    "she",
    "him",
    "her",
    "the",
    "a",
    "an",
})


@dataclass(frozen=True, slots=True)
class FocusCommand:
    """One parsed explicit focus switch."""

    #: ``None`` when the phrase named no kind ("talk about the pump") — the
    #: candidate search then covers every kind and the question mechanism
    #: disambiguates. A numeric id is honored ONLY with an explicit kind:
    #: bare numbers are never bound across kinds (machine 3 != task 3).
    kind: str | None
    #: The remaining reference text (name fragment) after the kind keyword.
    query: str
    #: A trailing/embedded number, when the phrase carried one ("machine 3").
    numeric_id: int | None


_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}

_ID_RE = re.compile(r"\b(\d{1,7})\b")
#: A WHOLE remainder that is nothing but digits is an explicit typed id
#: ("machine 3"); an embedded number ("machine Pump 12", "WO-000123") is part
#: of the NAME/REFERENCE and is never repaired into a PK.
_WHOLE_NUMERIC_RE = re.compile(r"^\d{1,7}$")

#: The spoken identity phrase per kind ("task 11" is the kanban_card 11).
KIND_PHRASES: dict[str, str] = {
    KIND_MACHINE: "machine",
    KIND_KANBAN_CARD: "task",
    KIND_WORK_ORDER: "work order",
}


def parse_focus_command(text: str) -> FocusCommand | None:
    """Parse an explicit switch phrase; ``None`` when the turn is not one.

    Deterministic and conservative: kind keyword first (``machine``/``asset``,
    ``task``/``card``, ``work order``/``job``/``order`` — the UI's "Task" is
    the ``kanban_card`` kind), then a number or a name fragment. Anything
    without a kind keyword is not a switch. ONLY a whole explicit typed
    numeric remainder is an id ("machine 3"); an embedded number is part of
    the reference/name ("machine Pump 12", "work order WO-000123") and its
    exact text is preserved — never stripped, repaired or re-tokenized.
    """
    if not isinstance(text, str):
        return None
    match = _SWITCH_RE.match(text)
    if match is None:
        return None
    target = match.group("target").strip()
    for pattern, kind in _KIND_PREFIX_RES:
        stripped = pattern.sub("", target, count=1)
        if stripped != target:
            remainder = stripped.strip()
            if _WHOLE_NUMERIC_RE.match(remainder):
                return FocusCommand(kind=kind, query="", numeric_id=int(remainder))
            word = remainder.strip(" .,?!").lower()
            if word in _NUMBER_WORDS:
                return FocusCommand(kind=kind, query="", numeric_id=_NUMBER_WORDS[word])
            return FocusCommand(kind=kind, query=remainder.strip(" .,"), numeric_id=None)
    # No kind keyword: a NAME reference across kinds. A bare number with no
    # kind is refused outright — it must never guess between machine 3,
    # task 3 and work order 3. A bare pronoun target is an anaphora ("tell
    # me about it"), not a switch — the anaphora path decides for those.
    target_clean = target.strip(" .,")
    if _ID_RE.fullmatch(target_clean) is not None:
        return None
    tokens = re.findall(r"[a-z]+", target_clean.lower())
    if tokens and all(token in _PRONOUN_TOKENS or len(token) < 3 for token in tokens):
        return None
    return FocusCommand(kind=None, query=target, numeric_id=None)


#: Tokens that can never be a record reference: pronouns, question verbs and
#: short conversational fillers. Any OTHER token ("pump", "station",
#: "machine", "seal", ...) is an explicit entity — a pronoun inside an
#: explicit reference must never rebind the turn to the stored focus.
_ANAPHORA_STOPWORDS = frozenset({
    "what",
    "when",
    "where",
    "who",
    "why",
    "how",
    "is",
    "are",
    "was",
    "were",
    "does",
    "did",
    "can",
    "could",
    "will",
    "would",
    "should",
    "tell",
    "me",
    "about",
    "status",
    "and",
    "or",
    "of",
    "the",
    "a",
    "an",
    "on",
    "in",
    "at",
    "to",
    "for",
    "with",
    "it",
    "its",
    "it's",
    "this",
    "that",
    "these",
    "those",
    "they",
    "them",
    "their",
    "he",
    "she",
    "him",
    "her",
    "one",
    "ones",
    "doing",
    "going",
    "look",
    "looking",
    "update",
    "news",
    "happening",
    "happened",
    "wrong",
    "now",
    "today",
    "new",
    "latest",
    "any",
    "some",
    "so",
    "then",
    "there",
    "here",
    "ok",
    "okay",
    "yeah",
    "yes",
    "please",
    "just",
    "very",
    "much",
    "more",
    "info",
    "information",
    "details",
    "again",
    "still",
    "all",
    "good",
    "fine",
})


def is_bare_anaphora(text: str) -> bool:
    """Whether the utterance is a pronoun follow-up with no explicit entity.

    Explicit references always win over pronouns; a turn naming a kind, a
    number or ANY record-like word is never an anaphor. Conservative: only a
    short pronoun/question/filler utterance is anaphoric, so an explicit
    entity is never overridden by a pronoun inside the same sentence.
    """
    if not isinstance(text, str):
        return False
    if parse_focus_command(text) is not None:
        return False
    if not _ANAPHORA_PRONOUN_RE.search(text):
        return False
    if not _ANAPHORA_REQUEST_RE.search(text):
        return False
    if _ID_RE.search(text):
        return False
    if len(text.split()) > _MAX_ANAPHORA_TOKENS:
        return False
    for token in re.findall(r"[a-z0-9']+", text.lower()):
        if token in _ANAPHORA_STOPWORDS or len(token) < 3:
            continue
        return False
    return True


def continuity_content(content: str, focus_snapshot: dict[str, Any] | None) -> str:
    """Reference continuity: bind a bare anaphor to the recorded focus.

    Mirrors the ``QuestionResolution.routing_content`` idiom: the routing
    input becomes "utterance — label (kind id)" where the projection is the
    server-resolved focus bound to THIS turn (the original snapshot on
    replay). The canonical id rides with the label so duplicate names can
    never choose a different record at the routing/tool boundary. Explicit
    references and non-anaphora turns are untouched.
    """
    from ai.core.focus.contract import focus_from_stored

    if not is_bare_anaphora(content) or not isinstance(focus_snapshot, dict):
        return content
    target = focus_from_stored(focus_snapshot.get("focus"))
    if target is None:
        return content
    phrase = KIND_PHRASES.get(target.kind, target.kind)
    return f"{content} — {target.label} ({phrase} {target.id})"


# ---- orchestration (application turn path) --------------------------------


def _focus_canonical(
    service, run: TurnRun, *, message: str, workflow: str, focus_changed: bool = False
) -> dict[str, Any]:
    """The terminal canonical for a focus turn (shape of the question rails).

    ``focus_changed`` marks a turn that ACTUALLY changed the durable focus:
    its recorded echo is the pre-change binding and the client must refresh
    ``GET /threads/{id}/focus`` (it is replayed verbatim on idempotent
    replay, so the marker survives).
    """
    from ai.core.turn.responses import _canonical_response_for_legacy
    from aichat.models import TurnModality, TurnState

    response = _canonical_response_for_legacy(message, speakable=run.modality == TurnModality.VOICE)
    return {
        "thread_id": run.thread.pk,
        "turn_id": run.turn.pk,
        "message": message,
        "agent": "root_workflow",
        "workflow_used": workflow,
        "response_state": TurnState.COMPLETE,
        "canonical_response": response.model_dump(mode="json"),
        "spoken_summary": response.spoken_summary,
        "reasoning_provenance": None,
        "route": run.route.to_dict() if getattr(run.route, "to_dict", None) else None,
        "focus_changed": bool(focus_changed),
    }


def _readback(label: str) -> str:
    """The target-confirmation utterance, generated from the projection only."""
    return f"Now talking about {label}."


def set_aside_pending_decision_sync(thread_id: Any) -> None:
    """Set aside any ARMED decision before an ACTUAL focus change/clear.

    The REAL coordinator policy decides what "set aside" means (only a
    PRESENTED decision advances to SET_ASIDE — confirmed/unknown effects
    stay on the receipt rails and are never retried or rolled back here).
    Fail CLOSED: if the decision cannot be set aside, the caller must abort
    the focus change — a later "yes" must never apply an old-target preview
    to the new focus. A disabled decision rail cannot hold an armed decision
    (``get_coordinator().present`` refuses on the same flag), so it is the
    documented no-op.
    """
    from ai.core.decisions import pipeline as decisions
    from ai.core.focus.contract import FocusSetAsideUnavailable

    if not decisions.enabled():
        return
    from ai.core.decisions.coordinator import get_coordinator

    try:
        get_coordinator().disarm(thread_id, "focus_switched", set_aside=True)
    except Exception as exc:  # fail closed: nothing is written on any failure
        raise FocusSetAsideUnavailable() from exc


async def _set_focus_now(service: NormalizedTurnService, run: TurnRun, ref: dict) -> dict | None:
    """Owner-bound focus write at the CURRENT version; None on any failure."""

    def _write():
        repository = service.repository_factory(run.actor, run.trusted_context)
        current = repository.get_focus(run.thread.pk)
        return repository.set_focus(
            run.thread.pk, ref, expected_focus_version=current["focus_version"]
        )

    try:
        return await service._call_sync(_write)
    except (FocusRejected, FocusValidationError):
        return None


async def maybe_handle(service: NormalizedTurnService, run: TurnRun) -> dict[str, Any] | None:
    """Handle an explicit switch or a no-focus clarification; else ``None``.

    Returning a canonical CAPTURES the turn (routing never sees it); the
    pending question window is closed by the caller exactly like the pending
    write-capture path closes it. A bare anaphor against a REMOVED/REVOKED
    focus gets an unavailable clarification — never unscoped guessing and
    never a fallback to a different record.
    """
    command = parse_focus_command(run.content)
    if command is not None:
        return await _handle_switch(service, run, command)
    if is_bare_anaphora(run.content):
        if not _snapshot_has_target(run):
            message = (
                "Which machine, task or work order do you mean? "
                "Say the target first, for example: talk about machine 3."
            )
            service._abandon_pending_question(thread_id=run.thread.pk)
            return _focus_canonical(service, run, message=message, workflow="focus_clarify")
        if not await _focus_snapshot_available(service, run):
            message = (
                "That target is no longer available. Which machine, task or work order do you mean?"
            )
            service._abandon_pending_question(thread_id=run.thread.pk)
            return _focus_canonical(service, run, message=message, workflow="focus_clarify")
    return None


def _snapshot_has_target(run: TurnRun) -> bool:
    """Whether this turn's recorded focus snapshot binds an actual target.

    A cleared focus (``{'focus': None, 'version': N}``) is NOT a target.
    """
    from ai.core.focus.contract import focus_from_stored

    snapshot = run.focus_snapshot if isinstance(run.focus_snapshot, dict) else {}
    return focus_from_stored(snapshot.get("focus")) is not None


async def _focus_snapshot_available(service: NormalizedTurnService, run: TurnRun) -> bool:
    """Re-validate this turn's recorded focus target against CURRENT access.

    Every read re-authorizes; a revoked/deleted record must surface as an
    unavailable clarification instead of quietly continuing on a stale
    label. The check runs as the turn's actor.
    """
    from ai.core.focus import contract as focus_contract
    from ai.core.focus import resolution as focus_resolution

    snapshot = run.focus_snapshot if isinstance(run.focus_snapshot, dict) else {}
    target = focus_contract.focus_from_stored(snapshot.get("focus"))
    if target is None:
        return False

    def _user():
        from django.contrib.auth import get_user_model

        return (
            get_user_model()
            .objects.filter(pk=getattr(run.actor, "user_pk", None), is_active=True)
            .first()
        )

    actor_user = await service._call_sync(_user)

    def _check():
        try:
            focus_resolution.resolve_focus_candidate(
                actor_user, focus_contract.FocusCandidate(kind=target.kind, id=target.id)
            )
        except focus_contract.FocusRejected:
            return False
        return True

    return bool(await service._call_sync(_check))


async def _handle_switch(
    service: NormalizedTurnService, run: TurnRun, command: FocusCommand
) -> dict[str, Any]:
    from ai.core.focus import resolution as focus_resolution

    # Any explicit switch captures the turn: the one-turn question window
    # closes exactly like the pending write-capture path closes it.
    service._abandon_pending_question(thread_id=run.thread.pk)

    def _user():
        from django.contrib.auth import get_user_model

        return (
            get_user_model()
            .objects.filter(pk=getattr(run.actor, "user_pk", None), is_active=True)
            .first()
        )

    actor_user = await service._call_sync(_user)
    page = await service._call_sync(
        focus_resolution.search_focus_candidates,
        actor_user,
        command.kind,
        command.query,
        command.numeric_id,
    )
    candidates = list(page.candidates)
    if not candidates:
        message = "I could not find an authorized target for that reference."
        return _focus_canonical(service, run, message=message, workflow="focus_switch")
    if len(candidates) > 1 or page.has_more:
        # Ambiguous (or a TRUNCATED page — never a false unique): the
        # existing persisted question mechanism decides. Each option carries
        # a server-owned ref; only a confirmed selection of one of these
        # updates focus.
        from ai.core.questions.promotion import set_question_proposal

        options = candidates[:4]
        set_question_proposal({
            "source": FOCUS_QUESTION_SOURCE,
            "question_text": "Which one do you mean?",
            "options": [
                {
                    "id": f"focus:{candidate.kind}:{candidate.id}",
                    "label": candidate.label,
                    "ref": {"kind": candidate.kind, "id": candidate.id},
                }
                for candidate in options
            ],
        })
        if page.has_more:
            message = "I found several matches. Which one do you mean?"
        else:
            message = f"I found {len(options)} matches. Which one do you mean?"
        return _focus_canonical(service, run, message=message, workflow="focus_clarify")
    chosen = candidates[0]
    try:
        result = await _set_focus_now(service, run, {"kind": chosen.kind, "id": chosen.id})
    except FocusOutOfAnalysisScope:
        message = (
            "That target is outside this conversation's analysis scope. "
            "The conversation focus is unchanged — change the analysis scope "
            "or start a new conversation."
        )
        return _focus_canonical(service, run, message=message, workflow="focus_switch")
    except FocusSetAsideUnavailable:
        message = (
            "I could not set the pending confirmation aside, "
            "so the conversation focus is unchanged."
        )
        return _focus_canonical(service, run, message=message, workflow="focus_switch")
    if result is None or result.get("focus") is None:
        message = "That target is not available. The conversation focus is unchanged."
        return _focus_canonical(service, run, message=message, workflow="focus_switch")
    run.focus_changed = True
    return _focus_canonical(
        service,
        run,
        message=_readback(str(result["focus"]["label"])),
        workflow="focus_switch",
        focus_changed=True,
    )


async def apply_question_answer(
    service: NormalizedTurnService, run: TurnRun
) -> dict[str, Any] | None:
    """Apply a CONFIRMED focus-selection option; everything else is unchanged.

    Only the persisted, server-owned option ref can update focus — and it is
    re-authorized on application (access may have been revoked since the
    option was built). Unmatched, expired or declined outcomes (and answers
    to any other question source) leave focus exactly as it was.
    """
    resolution = getattr(run, "question_resolution", None)
    if resolution is None or resolution.outcome != "selected":
        return None
    record = resolution.record or {}
    if str(record.get("source") or "") != FOCUS_QUESTION_SOURCE:
        return None
    options = record.get("options") or []
    index = resolution.interpretation.option_index
    if index is None or index >= len(options):
        return None
    ref = options[index].get("ref")
    try:
        candidate = normalize_focus_request(ref)
    except FocusValidationError:
        return None
    try:
        result = await _set_focus_now(service, run, {"kind": candidate.kind, "id": candidate.id})
    except FocusOutOfAnalysisScope:
        message = (
            "That target is outside this conversation's analysis scope. "
            "The conversation focus is unchanged — change the analysis scope "
            "or start a new conversation."
        )
        return _focus_canonical(service, run, message=message, workflow="focus_switch")
    except FocusSetAsideUnavailable:
        message = (
            "I could not set the pending confirmation aside, "
            "so the conversation focus is unchanged."
        )
        return _focus_canonical(service, run, message=message, workflow="focus_switch")
    if result is None or result.get("focus") is None:
        message = "That target is no longer available. The conversation focus is unchanged."
        return _focus_canonical(service, run, message=message, workflow="focus_switch")
    run.focus_changed = True
    return _focus_canonical(
        service,
        run,
        message=_readback(str(result["focus"]["label"])),
        workflow="focus_switch",
        focus_changed=True,
    )


__all__ = [
    "FOCUS_QUESTION_SOURCE",
    "KIND_PHRASES",
    "FocusCommand",
    "apply_question_answer",
    "continuity_content",
    "is_bare_anaphora",
    "maybe_handle",
    "parse_focus_command",
    "set_aside_pending_decision_sync",
]
