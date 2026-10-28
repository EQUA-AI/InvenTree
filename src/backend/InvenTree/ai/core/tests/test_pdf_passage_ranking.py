"""Literal-PDF ranking regressions for misleading contents/header citations."""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from ai.core.integrations.local_controlled_documents import _page_hits


def _reader(texts, *, encrypted=False):
    return SimpleNamespace(
        is_encrypted=encrypted,
        pages=[SimpleNamespace(extract_text=lambda text=text: text) for text in texts],
    )


class PDFPassageRankingTest(TestCase):
    def test_actual_filter_passage_preserves_conditions_and_follow_up_handling(self):
        """A focused read can recover the complete conditional method, literally."""
        source = json.loads(
            (Path(__file__).parent / "fixtures/voice_manual_method_conditions_p41.json").read_text()
        )
        text = source["text"]
        self.assertEqual(
            hashlib.sha256(text.encode()).hexdigest(), source["source_extraction_sha256"]
        )
        with patch("pypdf.PdfReader", return_value=_reader([""] * 40 + [text])):
            hits = _page_hits(b"source", ("filters", "grease", "petroleum", "dry", "twist"))
        self.assertEqual(len(hits), 1)
        _, page, excerpt = hits[0]
        self.assertEqual(page, source["physical_page"])
        self.assertIn(excerpt, text)
        self.assertLessEqual(len(excerpt), 1600)
        normalized = " ".join(excerpt.split())
        for condition in (
            "If the dust is difficult to remove",
            "dry them in the horizontal position",
            "In case the filters are impregnated with dust containing grease",
            "another petroleum solvent",
            "All the filters must be dried after the cleaning. Do not twist them",
        ):
            self.assertIn(condition, normalized)

    def test_contents_and_continued_dot_leader_pages_do_not_supply_instructions(self):
        contents = "TABLE OF CONTENTS\nCooling cleaning safety PPE tools..........25\n"
        continued = "\n".join(f"Cooling cleaning section {i}.............25" for i in range(6))
        actual = "Air/air heat exchanger cleaning. Fouling impairs heat exchange. Follow the applicable cleaning procedure."
        with patch("pypdf.PdfReader", return_value=_reader([contents, continued, actual])):
            hits = _page_hits(b"source", ("cooling", "cleaning", "fouling"))
        self.assertEqual([page for _, page, _ in hits], [3])
        self.assertEqual(hits[0][2], actual)

    def test_dense_real_passage_wins_over_distant_running_header_hit(self):
        text = "Motor installation manual\n" + "unrelated text " * 180
        text += "\nAir air heat exchanger cleaning: fouling restricts cooling.\n"
        with patch("pypdf.PdfReader", return_value=_reader([text])):
            hits = _page_hits(
                b"source", ("motor", "heat", "exchanger", "cleaning", "fouling", "cooling")
            )
        self.assertEqual(len(hits), 1)
        _, page, excerpt = hits[0]
        self.assertEqual(page, 1)
        self.assertIn("fouling restricts cooling", excerpt)
        self.assertIn(excerpt, text)
        self.assertLessEqual(len(excerpt), 1600)

    def test_common_header_word_does_not_outrank_specific_maintenance_passage(self):
        pages = ["Motor manual installation."] * 4 + [
            "Motor manual. Fouling of the heat exchanger restricts cooling."
        ]
        with patch("pypdf.PdfReader", return_value=_reader(pages)):
            hits = _page_hits(b"source", ("motor", "fouling", "cooling"))
        ordered = sorted(hits, key=lambda hit: (-hit[0], hit[1]))
        self.assertEqual(ordered[0][1], 5)
        self.assertIn(ordered[0][2], pages[4])

    def test_encryption_and_page_limit_still_fail_closed(self):
        for reader in (_reader(["Cooling"], encrypted=True), _reader(["Cooling"] * 201)):
            with patch("pypdf.PdfReader", return_value=reader):
                self.assertEqual(_page_hits(b"source", ("cooling",)), [])

    def test_no_query_or_only_stop_words_returns_no_source(self):
        with patch("pypdf.PdfReader") as reader:
            self.assertEqual(_page_hits(b"source", ()), [])
            self.assertEqual(_page_hits(b"source", ("the", "manual")), [])
        reader.assert_not_called()
