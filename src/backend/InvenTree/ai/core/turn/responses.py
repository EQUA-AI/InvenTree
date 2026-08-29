"""Canonical-response builders for the normalized turn pipeline (S47).

Moved verbatim from ``ai.core.turn_service``; the facade re-exports every
name so existing imports (including wf8's spoken-summary ceiling) keep
working.
"""

from __future__ import annotations

import re
import unicodedata

from ai.core.reasoning.schemas import CanonicalTurnResponse
from pydantic import ValidationError

#: Ceiling for a spoken legacy answer. Only the complete plain text is ever
#: spoken — clipping could drop a safety qualifier mid-claim, so an answer
#: that does not fit is honestly not spoken at all.
_SPOKEN_SUMMARY_MAX_CHARS = 700

_LEGACY_REASONING_SUMMARY = (
    "This text was produced by the selected legacy workflow. No hidden reasoning was persisted."
)


def _plain_spoken_text(message: str) -> str:
    """Reduce workflow markdown to the plain text the spoken schema accepts.

    Table rows and rule lines read as word-soup when spoken, so they are
    dropped entirely; removing text can only tighten the entailment check.
    """
    prose_lines = [
        line
        for line in message.splitlines()
        if not re.match(r"^\s*\|", line) and not re.match(r"^\s*[-=|:\s]{3,}$", line)
    ]
    text = re.sub(r"```.*?```", " ", "\n".join(prose_lines), flags=re.DOTALL)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)
    text = re.sub(r"\[([^\]\n]+)\]\([^)\n]+\)", r"\1", text)
    text = re.sub(r"</?[A-Za-z][^>\n]*>", " ", text)
    text = re.sub(r"^\s{0,3}(?:#{1,6}\s+|>\s?|[-+*]\s+|\d+[.)]\s+)", " ", text, flags=re.MULTILINE)
    text = re.sub(r"[`|*_~#>]", " ", text)
    text = "".join(
        " " if unicodedata.category(character).startswith("C") else character for character in text
    )
    return " ".join(text.split())


def _speakable_summary_candidates(message: str) -> tuple[str, ...]:
    """Return spoken-summary candidates derived only from the visible answer.

    Deriving from the answer text keeps the schema's lexical-entailment check
    satisfiable by construction. Truncation is deliberately never attempted:
    a clip can silently drop a qualifier ("... only if the machine is locked
    out") and speak a stronger claim than the visible answer makes.
    """
    plain = _plain_spoken_text(message)
    if not plain or len(plain) > _SPOKEN_SUMMARY_MAX_CHARS:
        return ()
    return (plain,)


def _canonical_response_for_legacy(
    message: str, *, speakable: bool = False
) -> CanonicalTurnResponse:
    """Adapt existing workflow text without changing its visible rendering.

    For voice-modality turns, attempt a schema-valid spoken summary derived
    from the answer itself so simple queries are spoken back. Every candidate
    must pass the full canonical validators (plain text, lexical entailment,
    caveat preservation); when none does, the turn stays honestly silent.
    """
    if speakable:
        for summary in _speakable_summary_candidates(message):
            try:
                return CanonicalTurnResponse(
                    kind="legacy_chat",
                    response_version=1,
                    response_state="complete",
                    detailed_response=message or "No response was produced.",
                    spoken_summary=summary,
                    reasoning_summary=_LEGACY_REASONING_SUMMARY,
                    confidence="low",
                    evidence=[],
                    next_questions=[],
                    recommended_actions=[],
                    safety_boundary="No additional safety boundary.",
                    speak=True,
                )
            except ValidationError:
                continue
    return CanonicalTurnResponse(
        kind="legacy_chat",
        response_version=1,
        response_state="complete",
        detailed_response=message or "No response was produced.",
        spoken_summary="",
        reasoning_summary=_LEGACY_REASONING_SUMMARY,
        confidence="low",
        evidence=[],
        next_questions=[],
        recommended_actions=[],
        safety_boundary=("No safety status was inferred; check the authoritative safety surface."),
        speak=False,
    )


def _canonical_terminal_response(state: str, message: str) -> CanonicalTurnResponse:
    """Return a strict, non-speaking response for a non-complete lifecycle state."""
    state_value = str(getattr(state, "value", state))
    return CanonicalTurnResponse(
        kind="repair_diagnosis",
        response_version=1,
        response_state=state_value,
        detailed_response=message,
        spoken_summary="",
        reasoning_summary=("The normalized turn ended without a complete diagnostic answer."),
        confidence="low",
        evidence=[],
        next_questions=[],
        recommended_actions=[],
        safety_boundary=("No safety status was inferred; check the authoritative safety surface."),
        speak=False,
    )


def _canonical_analysis_unavailable(*, locale: str = "en") -> CanonicalTurnResponse:
    """The analysis rail's honest abstention (S3; also the rollback posture).

    Until the evidence gate (Phase 4) gives RouteMode.ANALYSIS something
    real to execute — and whenever an incident disables the executor — an
    analysis-routed turn states plainly that the analysis did not run,
    rather than estimating from partial data. ``incomplete`` is structurally
    barred from actions and speech by the canonical schema validators.
    """
    del locale  # English-only v1; the template table owns future locales.
    return CanonicalTurnResponse(
        kind="evidence_analysis_unavailable",
        response_version=1,
        response_state="incomplete",
        detailed_response=(
            "Fleet and history analysis is not enabled on this assistant "
            "yet. I did not run the aggregate, trend, or record analysis "
            "you asked for, and I won't estimate it from partial data. You "
            "can review work orders and maintenance records directly in "
            "the maintenance screens, or ask me about a specific machine, "
            "part, or document instead."
        ),
        spoken_summary="",
        reasoning_summary=(
            "A read-only analysis intent was routed to the analysis rail, "
            "which is not enabled; no data was retrieved or estimated."
        ),
        confidence="low",
        evidence=[],
        next_questions=[],
        recommended_actions=[],
        safety_boundary=("No safety status was inferred; check the authoritative safety surface."),
        speak=False,
    )


def _canonical_analysis_capability_boundary(*, locale: str = "en") -> CanonicalTurnResponse:
    """Tier-2/3 analysis intents under a lit gate: typed capability boundary.

    Aggregate/trend/comparison analysis stays off the pilot path (S7/S9);
    when the S10 gate is on and a Tier-1 intent would execute, these intents
    get an honest, deterministic boundary instead of the generic abstention.
    """
    from ai.core import i18n_templates as i18n

    return CanonicalTurnResponse(
        kind="evidence_analysis_unavailable",
        response_version=1,
        response_state="incomplete",
        detailed_response=i18n.deterministic_template(i18n.ANALYSIS_CAPABILITY_BOUNDARY, locale),
        spoken_summary="",
        reasoning_summary=(
            "The requested analysis tier is not yet enabled; nothing was retrieved or estimated."
        ),
        confidence="low",
        evidence=[],
        next_questions=[],
        recommended_actions=[],
        safety_boundary=("No safety status was inferred; check the authoritative safety surface."),
        speak=False,
    )


def _canonical_advisory_intent(
    *, voice: bool = False, action_available: bool = False, locale: str = "en"
) -> CanonicalTurnResponse:
    """Explain effect wording without creating a proposal or executable action.

    S33: every string comes from the deterministic per-locale template
    tables; an unknown locale degrades to English inside the lookup.
    """
    from ai.core import i18n_templates as i18n

    safety_boundary = i18n.deterministic_template(i18n.SAFETY_BOUNDARY, locale)
    if voice:
        key = i18n.ADVISORY_VOICE_ACTION if action_available else i18n.ADVISORY_VOICE_READONLY
        message = i18n.deterministic_template(key, locale).format(safety=safety_boundary)
    else:
        message = i18n.deterministic_template(i18n.ADVISORY_TEXT, locale)
    return CanonicalTurnResponse(
        kind="advisory_intent",
        response_version=1,
        response_state="complete",
        detailed_response=message,
        spoken_summary=message if voice else "",
        reasoning_summary=("Effect-shaped wording was isolated as advisory intent only."),
        confidence="high",
        evidence=[],
        next_questions=[i18n.deterministic_template(i18n.ADVISORY_NEXT_QUESTION, locale)],
        recommended_actions=[],
        safety_boundary=safety_boundary,
        speak=voice,
    )


def _canonical_safety_refusal(*, voice: bool = False, locale: str = "en") -> CanonicalTurnResponse:
    """The deterministic unsafe-shortcut refusal (S4).

    ``complete`` — the refusal IS the whole answer (injection-refusal
    precedent), and nothing else may follow it: no RCA, no diagnosis, no
    parts, no timeline (the Q86 gate). Voice SPEAKS it — an eyes-free
    technician must hear the refusal — with the safety boundary appended so
    the spoken summary entails the visible text in token order.
    """
    from ai.core import i18n_templates as i18n

    safety_boundary = i18n.deterministic_template(i18n.SAFETY_BOUNDARY, locale)
    message = i18n.deterministic_template(i18n.SAFETY_SHORTCUT_REFUSAL, locale)
    if voice:
        # Q30: the pilot safety refusal is English-only. The refusal body
        # already falls back to English for every locale; the appended
        # boundary must not reintroduce a mixed-language spoken response,
        # and the visible text must stay entailed by the spoken summary in
        # token order — so the voice response uses the English boundary
        # throughout.
        safety_boundary = i18n.deterministic_template(i18n.SAFETY_BOUNDARY, "en")
        message = f"{message} {safety_boundary}"
    return CanonicalTurnResponse(
        kind="safety_shortcut_refusal",
        response_version=1,
        response_state="complete",
        detailed_response=message,
        spoken_summary=message if voice else "",
        reasoning_summary=("A request to bypass a safety control was refused deterministically."),
        confidence="high",
        evidence=[],
        next_questions=[],
        recommended_actions=[],
        safety_boundary=safety_boundary,
        speak=voice,
    )


def _canonical_voice_write(message: str) -> CanonicalTurnResponse:
    """A spoken voice write-confirmation read-back or outcome (Phase 4).

    ``message`` is always server-authored -- an exact read-back of a resolved
    action, or a fixed outcome phrase from the confirmation allow-list. It is
    spoken (an eyes-free technician must hear it) and the spoken summary equals
    the visible text, so speech adds nothing the record does not show.
    """
    return CanonicalTurnResponse(
        kind="voice_write_confirmation",
        response_version=1,
        response_state="complete",
        detailed_response=message,
        spoken_summary=message,
        reasoning_summary="Voice write-confirmation gate response.",
        confidence="high",
        evidence=[],
        next_questions=[],
        recommended_actions=[],
        safety_boundary="None",
        speak=True,
    )
