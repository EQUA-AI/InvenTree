"""Saved planning receipts retain complete conditions and their review boundary."""

import unittest

from ai.core.decisions.receipts import spoken_receipt


class RepairScopeSpokenReceiptTest(unittest.TestCase):
    """Exercise the real receipt with long conditional scope content."""

    def receipt(self, *, steps, tools):
        """Build only the fields consumed from the native saved receipt."""
        return spoken_receipt({
            "execution_state": "succeeded",
            "target_label": "WO-003993 Pump 08",
            "receipt": {
                "command": "save_proposed_repair_scope_draft",
                "draft": {
                    "id": 5768,
                    "revision": 1,
                    "scope": {
                        "steps": steps,
                        "tools_and_supplies": tools,
                    },
                },
            },
        })

    def test_complete_items_do_not_leave_partial_tool_or_condition(self):
        """The observed third-tool fragment must never reach native speech."""
        tools = [
            "Compressed air source (proposed planning requirement; confirm site safety).",
            "Soft round brass-wire brush (for aluminum tubes, if needed; planning proposal).",
            "General-purpose brush (for other surfaces, if accessible; planning proposal).",
        ]
        result = self.receipt(
            steps=["Review isolation before work.", "Inspect accessible surfaces."], tools=tools
        )
        self.assertIn("confirm site safety)", result)
        self.assertNotIn("Ge.", result)
        self.assertNotIn(".,", result)
        self.assertNotIn("..", result)
        self.assertIn("Supervisor review and site-safety authorization remain pending.", result)
        self.assertIn("Physical work has not started.", result)

    def test_oversized_first_item_is_referred_to_not_cut_or_skipped(self):
        """Never retain cleaning while losing its long prerequisite."""
        long_step = "Confirm the installed cooler and the approved procedure " + (
            "before any cleaning " * 20
        )
        result = self.receipt(
            steps=[long_step, "Clean the cooler."],
            tools=["An exceptionally long conditional tool requirement " * 8],
        )
        self.assertIn("proposed sequence is listed in the saved draft.", result)
        self.assertNotIn("Clean the cooler", result)
        self.assertIn("review the proposed list in the saved draft.", result)
        self.assertIn("draft 5768, revision 1.", result)
        self.assertIn("Zero replacement parts.", result)

    def test_short_complete_planning_steps_and_tools_keep_order(self):
        """Normal readback still names actual saved items in source order."""
        result = self.receipt(
            steps=["Confirm fitted cooler.", "Review approved method."],
            tools=["Inspection light.", "Approved brush."],
        )
        self.assertIn("Confirm fitted cooler. Review approved method.", result)
        self.assertIn("Tools and supplies: Inspection light; Approved brush.", result)
