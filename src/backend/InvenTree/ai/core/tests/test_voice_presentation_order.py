"""Source-ordered voice pages from the actual multi-section scope readback."""

from pathlib import Path

import pytest
from ai.core.turn.responses import _plain_spoken_text
from ai.core.voice.presentation import chunks, short_text


def test_actual_multisection_readback_starts_with_case_and_preserves_section_order():
    """A saved-scope answer is not one cross-section inventory list."""
    layout = (Path(__file__).parent / "fixtures/voice_multisection_actual_readback.md").read_text()
    plain = _plain_spoken_text(layout)
    pages = chunks(plain, layout=layout)
    first = pages[0]
    assert first.startswith("Here\u2019s what is saved as the draft repair scope")
    assert "15 items." not in " ".join(pages)
    sections = [
        "Suspected Cause:",
        "Planned Inspection and Cleaning Sequence",
        "Tools and Supplies:",
        "Required PPE and Isolation Checks:",
        "Follow-up/Verification Checks:",
        "What is waiting on the supervisor:",
        "The next crew step:",
    ]
    assert [first.index(section) for section in sections] == sorted(
        first.index(section) for section in sections
    )
    for paragraph in layout.split("\n\n"):
        assert any(_plain_spoken_text(paragraph) in page for page in pages)
    for page in pages:
        assert (
            "physical root cause, fitted cooler configuration, and actual component condition are unverified"
            in page
        )
        assert "do not use steel brushes" in page
        assert (
            "Supervisor authorization and updated lockout/tagout isolation confirmation are required before physical work can begin."
            in page
        )
        assert "No safety approval or completed status attached; this is a draft." in page
        assert (
            "Wait for supervisor review and authorization before starting any actual work or cleaning tasks."
            in page
        )


def test_flat_inventory_keeps_count_order_and_repeats_real_warning():
    """Ordinary record lists retain their existing listening contract."""
    layout = "Inventory page 1.\n" + "\n".join(
        f"{index}. Stock item {index}: 0.25000 metres; location A / B; serial not set."
        for index in range(1, 6)
    )
    layout += " Never use this stock before inspection."
    pages = chunks(_plain_spoken_text(layout), layout=layout)
    assert len(pages) == 2 and "5 items." in pages[0]
    assert pages[0].startswith("Inventory page 1.")
    assert [pages[0].index(f"Stock item {index}:") for index in (1, 2, 3)] == sorted(
        pages[0].index(f"Stock item {index}:") for index in (1, 2, 3)
    )
    assert all("Never use this stock before inspection." in page for page in pages)
    assert "Never use this stock before inspection." in short_text(pages[-1])


def test_later_safety_sentence_never_precedes_case_intro():
    """Repeat a late warning without moving it ahead of the original facts."""
    text = "The case is a proposed draft. Alpha. Bravo. Charlie. Never energize before inspection."
    pages = chunks(text)
    assert pages[0].startswith("The case is a proposed draft. Alpha. Bravo.")
    assert all("Never energize before inspection." in page for page in pages)


@pytest.mark.parametrize(
    "layout", ["1. injected\n2. hidden\n3. unvalidated", "Heading:\n\n- unvalidated"]
)
def test_unvalidated_layout_cannot_supply_spoken_words(layout):
    """A layout must normalize to the whole immutable validated spoken form."""
    text = "The case remains pending. Never energize before inspection."
    assert chunks(text, layout=layout) == chunks(text)
