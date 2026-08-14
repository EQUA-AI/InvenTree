"""Fast-path Tier-1 voice answers: permission gate + spoken formatting."""

from __future__ import annotations

import asyncio
import os
from unittest.mock import AsyncMock

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "ai.core.tests.settings")

import django

django.setup()

from ai.core.agents.routing import FastPathRouter  # noqa: E402
from ai.core.workflows.fast_path import (  # noqa: E402
    fast_path_permitted,
    format_fast_path_answer,
    voice_fast_path_enabled,
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
        self.assertFalse(fast_path_permitted("stock_group", part_only))
        self.assertTrue(fast_path_permitted("stock_group", both))
        self.assertFalse(fast_path_permitted("location", part_only))
        self.assertTrue(fast_path_permitted("location", both))
        # part_details and bom need only part.view.
        self.assertTrue(fast_path_permitted("part_details", part_only))
        self.assertTrue(fast_path_permitted("bom", part_only))

    def test_unknown_type_and_empty_profile_denied(self):
        self.assertFalse(fast_path_permitted("wat", frozenset({("part", "view")})))
        self.assertFalse(fast_path_permitted("stock_check", frozenset()))

    def test_only_group_stock_bypasses_disabled_quality_flag(self):
        self.assertTrue(voice_fast_path_enabled({"type": "stock_group"}, global_enabled=False))
        self.assertFalse(voice_fast_path_enabled({"type": "stock_check"}, global_enabled=False))
        self.assertTrue(voice_fast_path_enabled({"type": "stock_check"}, global_enabled=True))


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

    def test_stock_group(self):
        self.assertEqual(
            format_fast_path_answer({
                "type": "stock_group",
                "label": "resistors",
                "total_quantity": 241291.0,
                "part_count": 48,
            }),
            "We have 241,291 resistors in stock across 48 parts.",
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


class FastPathGroupStockTests(SimpleTestCase):
    """Plural stock questions aggregate every complete fuzzy-search hit."""

    def test_resistor_question_sums_all_matching_parts(self):
        router = FastPathRouter()
        router._data_provider = type(
            "Provider",
            (),
            {
                "search_parts": AsyncMock(
                    return_value=[
                        {"pk": 1, "in_stock": 100000},
                        {"pk": 2, "in_stock": 141291},
                    ]
                )
            },
        )()

        decision = asyncio.run(
            router.try_fast_path("How many resistors do we have in stock?", "thread-voice")
        )

        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertEqual(decision.extracted_entities["category"], "stock_group")
        self.assertEqual(decision.fast_path_result["total_quantity"], 241291)
        self.assertEqual(decision.fast_path_result["part_count"], 2)

    def test_provider_cap_falls_back_instead_of_reporting_partial_total(self):
        router = FastPathRouter()
        router._data_provider = type(
            "Provider",
            (),
            {
                "search_parts": AsyncMock(
                    return_value=[{"pk": index, "in_stock": 1} for index in range(100)]
                )
            },
        )()

        decision = asyncio.run(
            router.try_fast_path("How many resistors do we have in stock?", "thread-voice")
        )
        self.assertIsNone(decision)
