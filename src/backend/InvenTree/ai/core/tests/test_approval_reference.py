"""Spoken approval references stay exact; normalization never infers a target."""

import pytest
from ai.core.decisions.adapters.approval_reference import reference, spoken_reference


@pytest.mark.parametrize("value", ["a1b2c3d4", "A 1 B 2 C 3 D 4", "a one b two c three d four"])
def test_explicit_spelling_round_trips(value):
    assert reference(value) == "a1b2c3d4"
    assert reference(spoken_reference("a1b2c3d4-abcd-abcd-abcd-123456789abc")) == "a1b2c3d4"


@pytest.mark.parametrize(
    "value",
    [
        "a1b2c3d4 but change it",
        "the first one",
        "a1b2c3d",
        "a1b2c3d4 or a1b2c3d5",
        "a to b two c three d four",
    ],
)
def test_ambiguity_and_qualified_references_refused(value):
    assert reference(value) is None
