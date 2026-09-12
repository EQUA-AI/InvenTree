"""Speak coded identifiers without converting quantities into digit sequences."""

import re


def spoken_target(label: str) -> str:
    """Spell coded references individually; leave amounts intact."""
    return re.sub(
        r"\b([A-Z]{1,5})-(\d+)\b",
        lambda match: " ".join(match[1]) + " " + " ".join(match[2]),
        label,
    )
