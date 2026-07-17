"""Contract tests for speakable legacy voice responses.

Voice-modality fast-path turns must produce a schema-valid spoken summary
derived only from the visible answer, so simple queries are spoken back
without weakening the canonical validators. Text turns stay silent.
"""

from __future__ import annotations

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

from ai.core.turn_service import (  # noqa: E402
    _SPOKEN_SUMMARY_MAX_CHARS,
    _canonical_response_for_legacy,
    _plain_spoken_text,
)
from django.test import SimpleTestCase  # noqa: E402


class PlainSpokenTextTests(SimpleTestCase):
    """Markdown reduction must yield schema-acceptable plain text."""

    def test_markdown_constructs_are_removed(self):
        message = (
            "# Stock summary\n\n"
            "The **M5 hex bolt** (`FAS-001`) has the highest stock at 480 units.\n\n"
            "| IPN | Stock |\n|---|---|\n| FAS-001 | 480 |\n\n"
            "- See [stock page](https://example.com/stock)\n"
            "```sql\nSELECT 1;\n```\n"
        )
        plain = _plain_spoken_text(message)
        for marker in ("#", "*", "`", "|", "[", "](", "\n"):
            self.assertNotIn(marker, plain)
        self.assertIn("M5 hex bolt", plain)
        self.assertIn("480 units", plain)

    def test_table_rows_are_not_spoken(self):
        message = (
            "The M5 hex bolt has the highest stock at 480 units.\n\n"
            "| Rank | Part | Stock |\n|---|---|---|\n| 2 | M4 washer | 310 |\n"
        )
        plain = _plain_spoken_text(message)
        self.assertIn("highest stock at 480 units", plain)
        self.assertNotIn("M4 washer", plain)
        self.assertNotIn("Rank", plain)

    def test_control_characters_become_spaces(self):
        self.assertEqual(_plain_spoken_text("a\tb\r\nc"), "a b c")


class SpeakableLegacyResponseTests(SimpleTestCase):
    """The voice fast path speaks; text and unspeakable turns stay silent."""

    def test_text_modality_stays_silent(self):
        response = _canonical_response_for_legacy("The M5 bolt has 480 in stock.")
        self.assertFalse(response.speak)
        self.assertEqual(response.spoken_summary, "")

    def test_voice_modality_speaks_the_answer(self):
        response = _canonical_response_for_legacy(
            "The **M5 hex bolt** has the highest stock with 480 units.",
            speakable=True,
        )
        self.assertTrue(response.speak)
        self.assertEqual(
            response.spoken_summary,
            "The M5 hex bolt has the highest stock with 480 units.",
        )
        self.assertEqual(response.response_state.value, "complete")

    def test_long_answer_is_never_clipped_and_stays_silent(self):
        # Clipping could drop a qualifier mid-claim, so an answer beyond the
        # spoken ceiling is not spoken at all.
        sentence = "The M5 hex bolt has the highest stock with 480 units. "
        response = _canonical_response_for_legacy(sentence * 30, speakable=True)
        self.assertFalse(response.speak)
        self.assertEqual(response.spoken_summary, "")

    def test_qualifier_in_a_long_sentence_is_spoken_in_full(self):
        message = (
            "You can restart the pump after replacing the seal, but only if "
            "the machine is locked out and fully de-energized first."
        )
        response = _canonical_response_for_legacy(message, speakable=True)
        self.assertTrue(response.speak)
        self.assertIn("locked out and fully de-energized", response.spoken_summary)
        self.assertLessEqual(len(response.spoken_summary), _SPOKEN_SUMMARY_MAX_CHARS)

    def test_uncertainty_marker_is_preserved_because_full_text_is_spoken(self):
        filler = "The M5 hex bolt has the highest stock level today. " * 10
        message = filler + "The count is approximately 480 units."
        response = _canonical_response_for_legacy(message, speakable=True)
        self.assertTrue(response.speak)
        self.assertIn("approximately", response.spoken_summary)

    def test_empty_answer_stays_silent(self):
        response = _canonical_response_for_legacy("", speakable=True)
        self.assertFalse(response.speak)
        self.assertEqual(response.detailed_response, "No response was produced.")
