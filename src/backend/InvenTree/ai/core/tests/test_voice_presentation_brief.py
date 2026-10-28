"""Complete bounded prose stays together; no actual ASR/TTS or business writes."""

import hashlib
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from ai.core.reasoning.schemas import CanonicalTurnResponse
from ai.core.turn.responses import _plain_spoken_text
from ai.core.voice.presentation import POLICY_VERSION, chunks


class BriefPresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = json.loads(
            (Path(__file__).parent / "fixtures/voice_actual_prose_briefs.json").read_text()
        )

    def canonical(self, cue):
        source = self.fixture["cases"][cue]["canonical_response"]
        if cue == "T03":
            # Retain the real speak=false source. This tests a separately
            # schema-validated full-visible-text engineering candidate only.
            self.assertFalse(source["speak"])
            self.assertEqual(source["spoken_summary"], "")
            source = dict(
                source, speak=True, spoken_summary=_plain_spoken_text(source["detailed_response"])
            )
        return CanonicalTurnResponse.model_validate_json(json.dumps(source))

    def assert_native_caller_preserves_one_complete_utterance(self, cue):
        from voice.services import presentation

        canonical = self.canonical(cue)
        utterance = SimpleNamespace(pk="unit-utterance", spoken_summary=canonical.spoken_summary)

        class FixtureSession:
            objects = MagicMock()
            pk = "unit-session"
            utterances = SimpleNamespace(get=MagicMock(return_value=utterance))

        result = SimpleNamespace(utterance_ids=[str(utterance.pk)], position=0)
        with (
            patch.object(presentation.VoicePresentation, "objects") as manager,
            patch.object(presentation, "persist_utterance", return_value=utterance) as persist,
        ):
            manager.filter.return_value.first.return_value = None
            manager.create.return_value = result
            # Execute the real native caller body. The transaction/repository
            # seams are isolated; there is no database or actual persistence.
            item, first = presentation.create.__wrapped__(
                session=FixtureSession(),
                turn_id=self.fixture["cases"][cue]["turn_id"],
                text=canonical.spoken_summary,
                layout=canonical.detailed_response,
                safety_boundary=canonical.safety_boundary,
            )
            self.assertIs(item, result)
            self.assertIs(first, utterance)
            self.assertEqual(persist.call_count, 1)
            self.assertEqual(persist.call_args.kwargs["spoken_summary"], canonical.spoken_summary)
            self.assertEqual(persist.call_args.kwargs["policy_version"], POLICY_VERSION)
            self.assertEqual(
                manager.create.call_args.kwargs["source_hash"],
                hashlib.sha256(canonical.spoken_summary.encode()).hexdigest(),
            )
            self.assertIn(canonical.safety_boundary, persist.call_args.kwargs["spoken_summary"])

    def test_actual_t01_keeps_sensor_load_cooling_and_boundary_in_exact_source_order(self):
        canonical = self.canonical("T01")
        self.assertEqual(len(canonical.spoken_summary.split()), 83)
        self.assertEqual(
            hashlib.sha256(canonical.spoken_summary.encode()).hexdigest(),
            "096af32dd5dcef44ee998c130e6b5a7087f4823e8c0b75ca77c2d87e78361c03",
        )
        pages = chunks(
            canonical.spoken_summary,
            layout=canonical.detailed_response,
            safety_boundary=canonical.safety_boundary,
        )
        self.assertEqual(pages, [canonical.spoken_summary])
        self.assertNotIn("Say next three", pages[0])
        self.assertEqual(pages[0].count(canonical.safety_boundary), 1)
        self.assertEqual(
            [row["id"] for row in self.fixture["actualT01Pages"]],
            ["f117d3d9-dc2f-4b32-98c7-afc05f9a0dc9", "478d71b6-1674-4d99-8741-9ff624f58bed"],
        )

    def test_actual_t01_through_native_presentation_create_caller(self):
        self.assert_native_caller_preserves_one_complete_utterance("T01")

    def test_actual_t03_full_visible_text_candidate_keeps_every_word_and_late_caveat(self):
        canonical = self.canonical("T03")
        self.assertEqual(len(canonical.spoken_summary.split()), 209)
        self.assertEqual(
            chunks(
                canonical.spoken_summary,
                layout=canonical.detailed_response,
                safety_boundary=canonical.safety_boundary,
            ),
            [canonical.spoken_summary],
        )
        self.assertIn("unverified", canonical.spoken_summary)
        self.assertIn(canonical.safety_boundary, canonical.spoken_summary)

    def test_actual_t03_candidate_through_native_presentation_create_caller(self):
        self.assert_native_caller_preserves_one_complete_utterance("T03")

    def test_240_word_prose_is_one_unchanged_page(self):
        text = " ".join(
            " ".join([word] * 40) + "."
            for word in ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot"]
        )
        self.assertEqual(len(text.split()), 240)
        self.assertEqual(chunks(text, layout=text), [text])

    def test_300_word_prose_is_one_unchanged_page(self):
        text = " ".join(
            " ".join([word] * 50) + "."
            for word in ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot"]
        )
        self.assertEqual(len(text.split()), 300)
        self.assertEqual(chunks(text, layout=text), [text])

    def test_301_word_prose_keeps_paging_and_complete_late_warning(self):
        warning = "Never energize before inspection."
        sentences = [
            " ".join([word] * count) + "."
            for word, count in [
                ("Alpha", 59),
                ("Bravo", 59),
                ("Charlie", 59),
                ("Delta", 60),
                ("Echo", 60),
            ]
        ]
        text = " ".join([*sentences, warning])
        self.assertEqual(len(text.split()), 301)
        pages = chunks(text, layout=text, safety_boundary=warning)
        self.assertEqual(len(pages), 2)
        self.assertTrue(all(warning in page for page in pages))
        self.assertIn("Say next three", pages[0])
        self.assertTrue(all(any(sentence in page for page in pages) for sentence in sentences))

    def test_short_six_item_list_still_has_two_ordered_pages(self):
        layout = "Records:\n" + "\n".join(
            f"{index}. {name}."
            for index, name in enumerate(
                ["Alpha", "Bravo", "Charlie", "Delta", "Echo", "Foxtrot"], 1
            )
        )
        text = _plain_spoken_text(layout)
        self.assertLess(len(text.split()), 240)
        pages = chunks(text, layout=layout)
        self.assertEqual(len(pages), 2)
        self.assertIn("6 items.", pages[0])
        self.assertNotIn("Delta", pages[0])
        self.assertIn("Delta", pages[1])

    def test_short_inventory_list_with_continuation_keeps_five_record_boundaries(self):
        """The native stock footer does not turn its whole list into one section."""
        layout = (
            "E live gasket, IPN E-LIVE. Inventory page 1.\n"
            + "\n".join(
                f"{index}. Stock item {index}: 0.25000 metres; location E live shelf; serial not set."
                for index in range(1, 6)
            )
            + "\nMore records are available. Say stock of part E-LIVE page 2."
        )
        text = _plain_spoken_text(layout)
        pages = chunks(text, layout=layout)
        self.assertLess(len(text.split()), 240)
        self.assertEqual(len(pages), 2)
        self.assertEqual([page.count("Stock item") for page in pages], [3, 2])
        self.assertEqual(pages[0].count("Inventory page 1."), 1)
        self.assertIn("0.25000 metres", pages[0])
        self.assertIn("More records are available. Say stock of part E-LIVE page 2.", pages[1])

    def test_short_single_item_layout_cannot_be_misclassified_as_prose(self):
        layout = "Record:\n- Alpha. Bravo. Charlie. Delta. Echo."
        text = _plain_spoken_text(layout)
        self.assertEqual(len(chunks(text, layout=layout)), 2)

    def test_short_multi_section_layout_keeps_section_order_and_paging(self):
        layout = "\n\n".join(
            f"{heading}:\n- {content}."
            for heading, content in [
                ("Cause", "Alpha"),
                ("Scope", "Bravo"),
                ("Tools", "Charlie"),
                ("Supplies", "Delta"),
                ("Review", "Echo"),
                ("Next", "Foxtrot"),
            ]
        )
        text = _plain_spoken_text(layout)
        pages = chunks(text, layout=layout)
        self.assertEqual(len(pages), 2)
        self.assertTrue(pages[0].startswith("Cause: Alpha. Scope: Bravo. Tools: Charlie."))
        self.assertTrue(pages[1].startswith("Supplies: Delta. Review: Echo. Next: Foxtrot."))

    def test_unvalidated_layout_never_supplies_or_replaces_words(self):
        text = "The case remains pending. Never energize before inspection."
        for layout in ["1. injected\n2. hidden\n3. unvalidated", "Heading:\n\n- unvalidated"]:
            self.assertEqual(chunks(text, layout=layout), [text])

    def test_policy_version_changes_with_persisted_page_contract(self):
        self.assertEqual(POLICY_VERSION, "voice-presentation-v4")


if __name__ == "__main__":
    unittest.main()
