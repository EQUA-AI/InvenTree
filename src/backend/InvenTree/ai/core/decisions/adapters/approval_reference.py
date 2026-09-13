"""Lossless spoken UUID-prefix normalization; never fuzzy target matching."""

import re

_DIGITS = dict(
    zip(
        ("zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"),
        "0123456789",
        strict=True,
    )
)
_LETTER_NAMES = {"ay": "a", "bee": "b", "see": "c", "dee": "d", "ee": "e", "eff": "f"}


def reference(value):
    """Accept hex characters or explicitly spoken digit names, nothing else."""
    if re.search(r"[^a-zA-Z0-9 ,.-]", value):
        return None
    pieces = []
    for token in re.findall(r"[a-z0-9]+", value.lower()):
        if token in _DIGITS:
            pieces.append(_DIGITS[token])
        elif token in _LETTER_NAMES:
            pieces.append(_LETTER_NAMES[token])
        elif re.fullmatch(r"[a-f0-9]+", token):
            pieces.append(token)
        else:
            return None
    result = "".join(pieces)
    return result if 8 <= len(result) <= 32 else None


def spoken_reference(value):
    """Spell the public eight-character prefix in deterministic en-US output."""
    names = {digit: name for name, digit in _DIGITS.items()}
    return " ".join(names.get(char, char.upper()) for char in str(value).replace("-", "")[:8])
