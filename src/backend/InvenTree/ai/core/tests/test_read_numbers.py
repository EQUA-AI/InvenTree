"""Typed and ASR forms share explicit numeric fields without repairing IPNs."""

import json
from pathlib import Path

import pytest
from ai.core.read_numbers import normalize_read_numbers

CASES = json.loads(
    (Path(__file__).resolve().parents[6] / "contrib/testing/voice-number-cases.json").read_text()
)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case["text"])
def test_shared_read_number_vectors(case):
    result = normalize_read_numbers(case["text"])
    assert result.text == case["canonical"]
    assert result.ambiguous is case.get("ambiguous", False)


@pytest.mark.parametrize(
    "text",
    [
        "part88A",
        "part 88.2",
        "part 002.01-PCBA",
        "serial eighty-eight",
        "a machine called One Hundred",
    ],
)
def test_non_field_numbers_and_identifiers_remain_literal(text):
    assert normalize_read_numbers(text).text == text
