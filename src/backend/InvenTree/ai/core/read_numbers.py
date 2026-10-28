"""Interpret numbers only in explicit read fields; preserve the raw utterance."""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

from common.spoken_numbers import NUMBER_WORDS_PATTERN, decimal_slot, integer_slot

_WORDS = NUMBER_WORDS_PATTERN + "|point"
_TOKEN = rf"(?:{_WORDS}|and(?=\s+(?:{_WORDS})\b))"
_SLOT = re.compile(
    r"(?P<label>\b(?:part(?:\s+id)?|work[ -]*order(?:\s+id)?|wo|"
    r"(?:machine|category|location|stock item)\s+id|first|"
    r"at least|at most|no more than|no less than|less than|fewer than|more than|"
    r"greater than|under|below|over|above|exactly)\s*[:#]?\s*)"
    rf"(?P<number>[0-9]+(?:,[0-9]+)*(?:\.[0-9]+)?|(?:{_TOKEN})\b(?:[ -]+{_TOKEN}\b)*)"
    r"(?![\w-]|\.\w)",
    re.I,
)


@dataclass(frozen=True)
class ReadNumbers:
    """An internal parse, never a replacement for the user's stored transcript."""

    text: str
    ambiguous: bool = False


def _number(value: str, *, identifier: bool) -> str | None:
    if identifier:
        number = integer_slot(value)
        return number if number is not None and int(number) > 0 else None
    number = decimal_slot(value)
    return str(Decimal(number)) if number is not None else None


def normalize_read_numbers(text: str) -> ReadNumbers:
    """Normalize typed IDs/comparisons only, not names, IPNs or free prose."""
    ambiguous = False

    def replace(match):
        nonlocal ambiguous
        label = match["label"].strip(" :#").lower()
        label = re.sub(r"work[ -]*order", "work order", label)
        identifier = bool(
            re.fullmatch(
                r"part(?:\s+id)?|work order(?:\s+id)?|wo|(?:machine|category|location|stock item)\s+id",
                label,
            )
        )
        parsed = _number(match["number"], identifier=identifier)
        if parsed is None:
            ambiguous = True
            return match[0]
        return label + " " + parsed

    return ReadNumbers(_SLOT.sub(replace, text), ambiguous)
