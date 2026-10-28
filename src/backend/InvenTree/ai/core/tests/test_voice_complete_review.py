"""Long ordinary requests retain targets and final negations in spoken review."""

import unittest

from ai.core.voice.prompts import (
    TRANSCRIPT_MAX_CHARS,
    TRANSCRIPT_REVIEW_OVERSIZED,
    compose_transcript_review,
)


class CompleteTranscriptReviewTest(unittest.TestCase):
    """Do not silently present a clipped request as a complete readback."""

    def test_actual_scope_request_keeps_its_final_negative_instructions(self):
        """The observed T05 tail must be spoken before confirmation is requested."""
        request = (
            "Good. Ames, prepare the repair scope in the same pump eight corrective work order. "
            "Use the inspection finding and the applicable motor manual. Include the cause we suspect, "
            "inspection and cleaning sequence, tools, supplies, required PPE, isolation checks, whether "
            "any parts are justified, and the checks needed after the work. Link the sources. "
            "Save it as a draft for supervisor review. Do not order parts, approve safety, or mark anything complete."
        )
        self.assertGreater(len(request), 240)
        self.assertIn(request, compose_transcript_review(request))
        self.assertIn(
            "Do not order parts, approve safety, or mark anything complete.",
            compose_transcript_review(request),
        )

    def test_request_at_bound_is_complete_and_over_bound_is_explicit(self):
        """Oversized requests require the full visible review, never a partial quote."""
        request = "a" * TRANSCRIPT_MAX_CHARS
        self.assertIn(request, compose_transcript_review(request))
        self.assertEqual(compose_transcript_review(request + "b"), TRANSCRIPT_REVIEW_OVERSIZED)
        self.assertNotIn("I heard:", compose_transcript_review(request + "b"))

    def test_controls_do_not_drop_the_final_target_or_negation(self):
        """Normalization keeps request meaning and localized long-review instructions."""
        spoken = compose_transcript_review("Pump 08\nMachine 102\x00 Do not complete. ")
        self.assertIn("Pump 08 Machine 102 Do not complete.", spoken)
        self.assertNotIn("\x00", spoken)
        self.assertIn(
            "transcripción en pantalla",
            compose_transcript_review("x" * (TRANSCRIPT_MAX_CHARS + 1), "es-MX"),
        )
