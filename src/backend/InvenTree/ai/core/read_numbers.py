"""Interpret numbers only in explicit read fields; preserve the raw utterance."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

_WORDS = (
    "zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    "thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|"
    "thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|point"
)
_TOKEN = rf"(?:{_WORDS}|and(?=\s+(?:{_WORDS})\b))"
_SLOT = re.compile(
    r"(?P<label>\b(?:part(?:\s+id)?|work\s*order(?:\s+id)?|wo|first|"
    r"at least|at most|no more than|no less than|less than|fewer than|more than|"
    r"greater than|under|below|over|above|exactly)\s*[:#]?\s*)"
    rf"(?P<number>\d+(?:,\d{{3}})*(?:\.\d+)?|(?:{_TOKEN})\b(?:[ -]+{_TOKEN}\b)*)"
    r"(?![\w-]|\.\w)",
    re.I,
)
_DIGITS = dict(
    zip(
        ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"],
        "0123456789",
        strict=True,
    )
)


@dataclass(frozen=True)
class ReadNumbers:
    """An internal parse, never a replacement for the user's stored transcript."""

    text: str
    ambiguous: bool = False


def _number(value: str, *, identifier: bool) -> str | None:
    from ai.core.decisions.resolver import _reference

    if re.fullmatch(r"\d+(?:\.\d+)?", value):
        if identifier and ("." in value or int(value) <= 0):
            return None
        return value
    words = value.lower().replace("-", " ").split()
    if len(words) > 24 or words.count("point") > 1:
        return None
    if "point" in words:
        if identifier:
            return None
        index = words.index("point")
        whole = _reference(" ".join(words[:index]))
        fraction = words[index + 1 :]
        if (
            not whole
            or not whole.isdigit()
            or not fraction
            or any(w not in _DIGITS for w in fraction)
        ):
            return None
        return str(Decimal(whole + "." + "".join(_DIGITS[w] for w in fraction)))
    number = _reference(value)
    if number is None or not number.isdigit() or (identifier and int(number) <= 0):
        return None
    return number


def normalize_read_numbers(text: str) -> ReadNumbers:
    """Normalize typed IDs/comparisons only, not names, IPNs or free prose."""
    ambiguous = False

    def replace(match):
        nonlocal ambiguous
        label = match["label"].strip(" :#").lower()
        identifier = bool(re.fullmatch(r"part(?:\s+id)?|work\s*order(?:\s+id)?|wo", label))
        parsed = _number(match["number"], identifier=identifier)
        if parsed is None:
            ambiguous = True
            return match[0]
        return label + " " + parsed

    return ReadNumbers(_SLOT.sub(replace, text), ambiguous)
