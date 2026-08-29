"""Deterministic unsafe-shortcut detection (S4, WP4).

The battery's Q86: the assistant refused to endorse skipping shutdown and
isolation controls — then generated 3,000 words of speculative root-cause
analysis. This guard makes the refusal deterministic, concise, and final:
a request to skip, bypass, shorten, or defeat a safety control is answered
by one fixed server-authored template and never reaches routing, a
workflow, or a model.

Pattern discipline mirrors ``ai.core.voice.injection``:

- content-only — no permission or tool state is consulted;
- every pattern pairs a BYPASS VERB class with a SAFETY-CONTROL NOUN class
  inside a bounded same-clause window, so "skip the intro section of the
  manual" or "bypass valve replacement history" can never match;
- benign guards are checked FIRST (document navigation, reading questions
  about what a procedure says — those are ``safety_lookup``, a legitimate
  intent);
- detection covers en/es/de/fr (decision record Q30) in one flat compiled
  tuple, always all applied — the turn's language metadata is untrusted
  and mixed-language turns exist. Responses are English-only in v1 via the
  ``SAFETY_SHORTCUT_REFUSAL`` template's unconditional English fallback.

Echo-channel rule (injection.py precedent): the refusal is a constant and
NEVER quotes or paraphrases the user's request.
"""

from __future__ import annotations

import re

#: Benign shapes checked before any unsafe pattern. Reading questions and
#: document navigation are legitimate; diagnosis of a tripping interlock is
#: a diagnostic question, not a bypass request.
_BENIGN_GUARDS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        # Document navigation: "skip to the troubleshooting section".
        r"\b(?:skip|jump|go)\b[^.?!]{0,20}\bto\b[^.?!]{0,30}\b(?:section|"
        r"chapter|page|step|part|troubleshooting|appendix|table|intro)",
        r"\bskip the (?:intro(?:duction)?|overview|preface|summary)\b",
        # Reading questions: "what does the lockout procedure say/require".
        r"\b(?:what|which|where|when|who)\b[^.?!]{0,60}\b(?:say|says|state|"
        r"states|require|requires|specify|specifies|recommend|recommends|"
        r"list|lists|mean|means)\b",
        # Diagnosis of a protective device is not a request to defeat it.
        r"\bwhy\b[^.?!]{0,60}\b(?:trip(?:s|ped|ping)?|activat\w+|"
        r"engag\w+|open(?:s|ed|ing)?|fir(?:e|es|ed|ing))\b",
        # Equipment nouns: "bypass valve", "isolation damper" as hardware.
        r"\b(?:bypass|isolation)\s+(?:valve|damper|line|switch|relay|"
        r"contactor|breaker)s?\b",
        # History about such hardware or events, not a live request.
        r"\b(?:replacement|maintenance|service|repair) history\b",
    )
)

_BYPASS_VERB = (
    # en
    r"(?:skip(?:ping)?|bypass(?:ing)?|defeat(?:ing)?|disabl\w+|overrid\w+|"  # codespell:ignore disabl
    r"short[- ]?cut(?:ting)?|shorten(?:ing)?|speed(?:ing)? up|"
    r"jumper(?:ing)?(?: out)?|cheat(?:ing)?|work(?:ing)? around|"
    r"get (?:around|past)|avoid(?:ing)?|omit(?:ting)?|ignor\w+|"
    # es
    r"saltar(?:se|nos|me)?|omitir|evitar|desactivar|puentear|acortar|anular|"
    # de
    r"überspringen|umgehen|abkürzen|deaktivieren|überbrücken|aushebeln|"
    # fr
    r"sauter|contourner|désactiver|raccourcir|shunter|neutraliser)"
)

_SAFETY_NOUN = (
    # en
    r"(?:lock[- ]?out(?:[- /]tag[- ]?out)?|tag[- ]?out|loto|isolation|"
    r"interlocks?|permits?(?: to work)?|guard(?:ing)?s?|"
    r"stored[- ]energy|discharge (?:wait|time|period)|bleed[- ]?down|"
    r"wait(?:ing)? (?:time|period)|de[- ]?energi[sz]\w+|"
    r"zero[- ]energy (?:check|verification|state)|"
    r"verif\w+ (?:of )?(?:isolation|zero|absence)|earthing|grounding|"
    r"ppe|protective (?:equipment|gear)|safety (?:procedures?|steps?|"
    r"checks?|precautions?|controls?|devices?)|"
    # es
    r"bloqueo|etiquetado|aislamiento|enclavamientos?|candados?|"
    r"energía almacenada|epp|verificación de (?:aislamiento|cero)|"
    # de
    r"verriegelung(?:en)?|freischaltung|absperrung|wartezeit(?:en)?|"
    r"restenergie|psa|sicherheitsabschaltung(?:en)?|erdung|"
    # fr
    r"consignation|verrouillage|isolement|attente de décharge|"
    r"énergie résiduelle|epi|mise à la terre)"
)

#: Bounded same-clause windows keep matching sentence-local and linear.
_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        # Bypass verb near a safety-control noun ("can we skip the lockout",
        # "is it ok to shorten the discharge wait").
        rf"\b{_BYPASS_VERB}\b[^.?!]{{0,40}}\b{_SAFETY_NOUN}\b",
        # Noun first: "the isolation can be skipped, right?" — and the
        # verb-final orders of German/French ("die Verriegelung umgehen",
        # "la consignation contourner"). Symmetric with pattern one; the
        # benign guards (checked first) keep equipment nouns and reading
        # questions out.
        rf"\b{_SAFETY_NOUN}\b[^.?!]{{0,40}}\b{_BYPASS_VERB}\b",
        rf"\b{_SAFETY_NOUN}\b[^.?!]{{0,40}}\b(?:can|could|may)\s+be\s+"
        rf"(?:skipped|bypassed|shortened|omitted|ignored|disabled)\b",
        # "without" family: work on it without isolating/locking out.
        rf"\b(?:without|sin|ohne|sans)\b[^.?!]{{0,40}}"
        rf"\b(?:isolat\w+|lock(?:ing)?[- ]?out|tag(?:ging)?[- ]?out|"
        rf"de[- ]?energi[sz]\w+|discharg\w+|waiting|grounding|earthing|"
        rf"{_SAFETY_NOUN})\b",
        # Live-work shapes: "leave it energized/live/running while I ...".
        r"\b(?:leave|keep|leaving|keeping)\b[^.?!]{0,30}\b(?:energi[sz]ed|"
        r"live|powered|running|pressuri[sz]ed)\b[^.?!]{0,40}\b(?:while|"
        r"and|so)\b[^.?!]{0,40}\b(?:work|open|reach|clean|adjust|touch|"
        r"repair|fix|test|check)\w*\b",
    )
)


def has_unsafe_shortcut(text: str) -> bool:
    """Whether ``text`` asks to skip/defeat a safety control.

    Falsy-safe and whitespace-normalized; benign guards win. Content-only:
    the result depends on nothing but the text, so it can never widen or
    narrow anyone's authority — only refuse to help with a shortcut.
    """
    if not text:
        return False
    normalized = " ".join(str(text).split())
    if any(pattern.search(normalized) for pattern in _BENIGN_GUARDS):
        return False
    return any(pattern.search(normalized) for pattern in _PATTERNS)


__all__ = ["has_unsafe_shortcut"]
