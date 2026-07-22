"""Fast-path Tier-1 voice answers: permission gate + spoken formatting."""

from __future__ import annotations

import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

from ai.core.workflows.fast_path import (  # noqa: E402
    fast_path_permitted,
    format_fast_path_answer,
)
from django.test import SimpleTestCase  # noqa: E402


class FastPathPermissionTests(SimpleTestCase):
    """The gate fails closed and requires every listed view permission."""

    def test_requires_all_listed_views(self):
        part_only = frozenset({("part", "view")})
        both = frozenset({("part", "view"), ("stock", "view")})
        # stock_check and location read parts *and* stock.
        self.assertFalse(fast_path_permitted("stock_check", part_only))
        self.assertTrue(fast_path_permitted("stock_check", both))
        self.assertFalse(fast_path_permitted("location", part_only))
        self.assertTrue(fast_path_permitted("location", both))
        # part_details and bom need only part.view.
        self.assertTrue(fast_path_permitted("part_details", part_only))
        self.assertTrue(fast_path_permitted("bom", part_only))

    def test_unknown_type_and_empty_profile_denied(self):
        self.assertFalse(fast_path_permitted("wat", frozenset({("part", "view")})))
        self.assertFalse(fast_path_permitted("stock_check", frozenset()))


class FastPathFormatTests(SimpleTestCase):
    """Rendering is concise and spoken-friendly; unrenderable -> None."""

    def test_stock_check(self):
        self.assertEqual(
            format_fast_path_answer({
                "type": "stock_check",
                "part": {"name": "M5 bolt"},
                "total_quantity": 42,
            }),
            "M5 bolt has 42 in stock.",
        )

    def test_part_details_with_and_without_description(self):
        self.assertEqual(
            format_fast_path_answer({
                "type": "part_details",
                "part": {"name": "Widget", "description": "A widget"},
            }),
            "Widget: A widget.",
        )
        self.assertEqual(
            format_fast_path_answer({"type": "part_details", "part": {"name": "Widget"}}),
            "Widget.",
        )

    def test_bom_pluralization(self):
        self.assertEqual(
            format_fast_path_answer({"type": "bom", "part": {"name": "Asm"}, "bom_items": [1, 2]}),
            "Asm has 2 BOM lines.",
        )
        self.assertEqual(
            format_fast_path_answer({"type": "bom", "part": {"name": "Asm"}, "bom_items": [1]}),
            "Asm has 1 BOM line.",
        )

    def test_location(self):
        self.assertEqual(
            format_fast_path_answer({
                "type": "location",
                "part": {"name": "Bolt"},
                "locations": [{"location": "A-3", "quantity": 5}],
            }),
            "Bolt is in A-3, with 5 on hand.",
        )
        self.assertEqual(
            format_fast_path_answer({
                "type": "location",
                "part": {"name": "Bolt"},
                "locations": [],
            }),
            "Bolt has no recorded stock location.",
        )

    def test_unrenderable_returns_none(self):
        self.assertIsNone(format_fast_path_answer({"type": "unknown"}))
        self.assertIsNone(format_fast_path_answer(None))
