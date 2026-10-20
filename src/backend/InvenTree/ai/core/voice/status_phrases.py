"""Versioned static status phrases for eyes-free voice sessions (§7.6).

Pure module. Field technicians may be unable to look at a screen, so every
turn outcome must be audible. Only these exact application-selected phrases
may be spoken as status; they carry no diagnosis, recommendation, or safety
content, and each is persisted as a ``VoiceUtterance`` before the exact-TTS
request, like any other speech.
"""

from __future__ import annotations

#: Recorded with each status utterance so a phrase change is auditable.
#: Bumped to v2 when the stage catalogue below was added (2026-10-05,
#: VoiceScopeLatencyFixPlan §5.1): rows keep their own policy version, so a
#: phrase-policy change stays auditable per utterance.
STATUS_PHRASE_POLICY_VERSION = "status-v2"

#: Spoken after a processing delay so the technician knows the turn landed.
THINKING = "Let me check that."

#: Spoken when a completed answer cannot be read aloud (too long or has no
#: schema-valid spoken form); the visible chat remains the full record.
ANSWER_IN_CHAT = "The answer is ready in the chat."

#: Spoken when the turn failed outright.
TURN_FAILED = "Sorry, I could not process that. Please try again."

#: Spoken when the turn ended without a complete answer (bounded review
#: exhausted, terminal replay); silence here would strand an eyes-free user.
ANSWER_INCOMPLETE = "I could not finish that answer. Please try again."

#: Spoken when the conversation's machine selection changed after this voice
#: session was bound to it (§13.3 pattern 10); the session refuses further
#: turns until the user acknowledges by restarting voice.
SCOPE_CHANGED = "The machine selection changed. Please restart voice to continue."
#: Voice-UX plan A3: spoken when an unrelated reply set a pending change aside.
#: The change was NOT applied; the reply is then answered normally. Because
#: the provider plays one app response at a time, the set-aside is spoken as
#: a prefix of whatever follows (see ``combine_status``).
SET_ASIDE = "I set the pending change aside; it was not applied."
SET_ASIDE_ANSWER_IN_CHAT = (
    "I set the pending change aside; it was not applied. The answer is ready in the chat."
)
SET_ASIDE_ANSWER_INCOMPLETE = (
    "I set the pending change aside; it was not applied. "
    "I could not finish that answer. Please try again."
)

#: DEPRECATED bridge constant for the not-yet-integrated route path (the
#: route currently sleeps this single value). The approved bounded schedule
#: lives in :mod:`ai.core.voice.progress` (first optional cue at 2 s after
#: admission, second no earlier than 8 s, minimum gap, maximum two per
#: turn); this constant now equals the first optional cue. The historical
#: 4 s value is retired -- see the contract delta recorded in
#: ``test_voice_latency.py``.
#: Raised from 2.5 s after measurement: 30 of 32 turns in the 2026-07-26 test
#: crossed that threshold, so "Let me check that" was the default experience
#: rather than a signal that something was taking unusually long. Social turns
#: and the bounded planner (see wf8 _is_social_turn and
#: voice_write_plan_timeout_s) now finish below this.
INTERIM_STATUS_DELAY_S = 2.0

# --------------------------------------------------------------------------- #
# Progress stage catalogue (§5.1). Allow-listed, localized, content-free.     #
# --------------------------------------------------------------------------- #
#: The verified application stages that may select a progress phrase. A
#: stage is only ever selected from an actual application signal (admission,
#: a real queue wait, an actually-started authorized read, ongoing work) --
#: the catalogue never fabricates progress and carries no target names.
#: ``failed``/``incomplete`` are the truthful terminal pair: their variants
#: are semantically distinct and are never rotated into each other.
PROGRESS_STAGES = (
    "admitted",
    "queued",
    "read_active",
    "still_working",
    "answer_in_chat",
    "failed",
    "incomplete",
)

#: Optional waiting speech (suppressed entirely by preference ``off``).
OPTIONAL_WAITING_STAGES = frozenset({"admitted", "queued", "read_active", "still_working"})

#: Required outcome speech (errors, pointers, readbacks): never suppressed
#: by the waiting-speech preference and outside the optional-prompt budget.
REQUIRED_OUTCOME_STAGES = frozenset({"answer_in_chat", "failed", "incomplete"})

#: Allow-listed variants per stage. All variants of a stage are truthfully
#: interchangeable; semantically distinct outcomes live in distinct stages.
STAGE_PHRASES: dict[str, tuple[str, ...]] = {
    "admitted": (
        "Got it.",
        "Okay, I'll check.",
        "One moment.",
    ),
    "queued": (
        "I'm finishing the previous request.",
        "Your request is queued.",
    ),
    "read_active": (
        "I'm checking the selected record.",
        "I'm checking the details.",
        "Let me look that up.",
    ),
    "still_working": (
        "I'm still working on that.",
        "This is taking a little longer.",
        "I'm still checking.",
    ),
    "answer_in_chat": (
        ANSWER_IN_CHAT,
        "Please check the chat for the result.",
    ),
    "failed": (TURN_FAILED,),
    "incomplete": (ANSWER_INCOMPLETE,),
}

#: S33: per-locale variants of the SAME phrases. Every entry joins the
#: allow-list below, so the persist-before-speak hash discipline is identical
#: in every language — a localized phrase is allow-listed or it is not spoken.
#: Keys are the English phrases (the canonical identity of each status).
LOCALIZED_STATUS_PHRASES: dict[str, dict[str, str]] = {
    "es": {
        THINKING: "Déjame comprobarlo.",
        ANSWER_IN_CHAT: "La respuesta está lista en el chat.",
        TURN_FAILED: "Lo siento, no pude procesarlo. Inténtalo de nuevo.",
        ANSWER_INCOMPLETE: ("No pude terminar esa respuesta. Inténtalo de nuevo."),
        SCOPE_CHANGED: ("La selección de máquinas cambió. Reinicia la voz para continuar."),
        SET_ASIDE: "Dejé el cambio pendiente a un lado; no se aplicó.",
        SET_ASIDE_ANSWER_IN_CHAT: (
            "Dejé el cambio pendiente a un lado; no se aplicó. La respuesta está lista en el chat."
        ),
        SET_ASIDE_ANSWER_INCOMPLETE: (
            "Dejé el cambio pendiente a un lado; no se aplicó. "
            "No pude terminar esa respuesta. Inténtalo de nuevo."
        ),
        # Progress stage catalogue (§5.1): every variant is localized; a
        # locale outside the allow-list keeps the deterministic English
        # fallback (see localized_status_phrase).
        "Got it.": "Entendido.",
        "Okay, I'll check.": "De acuerdo, voy a comprobarlo.",
        "One moment.": "Un momento.",
        "I'm finishing the previous request.": "Estoy terminando la solicitud anterior.",
        "Your request is queued.": "Tu solicitud está en cola.",
        "I'm checking the selected record.": "Estoy comprobando el registro seleccionado.",
        "I'm checking the details.": "Estoy comprobando los detalles.",
        "Let me look that up.": "Déjame buscarlo.",
        "I'm still working on that.": "Sigo trabajando en eso.",
        "This is taking a little longer.": "Esto está tardando un poco más.",
        "I'm still checking.": "Sigo comprobando.",
        "Please check the chat for the result.": (
            "Por favor, revisa el chat para ver el resultado."
        ),
    },
}

#: (pre-speech status, base status) -> the single allow-listed phrase that
#: says both, so no second ``response.create`` cancels the first.
_PRE_SPEECH_COMBINATIONS: dict[tuple[str, str], str] = {
    (SET_ASIDE, ANSWER_IN_CHAT): SET_ASIDE_ANSWER_IN_CHAT,
    (SET_ASIDE, ANSWER_INCOMPLETE): SET_ASIDE_ANSWER_INCOMPLETE,
}


def combine_status(pre_speech: str | None, base: str) -> str:
    """The one phrase that speaks ``pre_speech`` then ``base`` (or ``base`` alone)."""
    if not pre_speech:
        return base
    return _PRE_SPEECH_COMBINATIONS.get((pre_speech, base), base)


def localized_status_phrase(phrase: str, locale: str | None) -> str:
    """Return the locale's variant of a canonical status phrase.

    Unknown locales and unknown phrases fall back to the English input —
    a wrong-language phrase is worse than an English one, and the caller's
    allow-list check runs on the RETURNED value either way.
    """
    if not locale:
        return phrase
    table = LOCALIZED_STATUS_PHRASES.get(str(locale).lower().split("-")[0])
    if not table:
        return phrase
    return table.get(phrase, phrase)


class StatusPhraseSelector:
    """Deterministic per-session/stage rotation, never an immediate repeat.

    Pure and content-free: selection depends only on the session key, the
    stage and the number of selections so far -- never on transcript,
    targets, or any other content. The same session/stage/sequence always
    yields the same phrase, which keeps the audit trail reproducible.
    """

    def __init__(self, session_key: str = "") -> None:
        self._session_key = str(session_key)
        self._counts: dict[str, int] = {}
        self._last: dict[str, str] = {}

    def _offset(self, stage: str, size: int) -> int:
        import hashlib

        digest = hashlib.sha256(f"{self._session_key}:{stage}".encode()).digest()
        return digest[0] % size

    def select(self, stage: str, *, locale: str | None = None) -> str:
        variants = STAGE_PHRASES[stage]
        count = self._counts.get(stage, 0)
        offset = self._offset(stage, len(variants))
        phrase = variants[(offset + count) % len(variants)]
        if len(variants) > 1 and phrase == self._last.get(stage):
            phrase = variants[(offset + count + 1) % len(variants)]
        self._counts[stage] = count + 1
        self._last[stage] = phrase
        return localized_status_phrase(phrase, locale)


#: The complete allow-list; anything not present here is not a status phrase.
ALLOWED_STATUS_PHRASES = frozenset(
    {
        THINKING,
        ANSWER_IN_CHAT,
        TURN_FAILED,
        ANSWER_INCOMPLETE,
        SCOPE_CHANGED,
        SET_ASIDE,
        SET_ASIDE_ANSWER_IN_CHAT,
        SET_ASIDE_ANSWER_INCOMPLETE,
    }
    | {phrase for variants in STAGE_PHRASES.values() for phrase in variants}
    | {localized for table in LOCALIZED_STATUS_PHRASES.values() for localized in table.values()}
)
