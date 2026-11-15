"""Independent-review regressions for the unwired machine page primitive."""

import unittest

from assets.integration_machine_pages import MachinePage, MachinePageContext, PageCursor


class MachinePageReviewRegressionTests(unittest.TestCase):
    """Exercise boundedness and disclosure without trusting a constructed cursor."""

    def setUp(self):
        """Use synthetic non-sensitive page coordinates."""
        self.context = MachinePageContext(
            'synthetic', 1, 'scope', 'filter', 'generation'
        )

    def test_keys_reject_values_beyond_page_contract_bounds(self):
        """Name and ID magnitudes have explicit integration safety bounds."""
        for key in (('A' * 256, 1), ('A', 1 << 63)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                PageCursor.start(self.context, page_limit=1).accept(
                    MachinePage(self.context, (key,), None), requested_cursor=None
                )
        terminal = PageCursor.start(self.context, page_limit=1).accept(
            MachinePage(self.context, (('', (1 << 63) - 1),), None),
            requested_cursor=None,
        )
        self.assertTrue(terminal.provider_terminal)

    def test_directly_constructed_cursor_cannot_bypass_page_size_and_budget(self):
        """Invalid scalar state is rejected even without the factory method."""
        for state in (
            PageCursor(self.context, page_limit=101),
            PageCursor(self.context, page_limit='1'),
            PageCursor(self.context, page_limit=1, page_count=-1),
        ):
            with self.subTest(state=state), self.assertRaises(ValueError):
                state.accept(
                    MachinePage(self.context, (('A', 1),), None), requested_cursor=None
                )

    def test_auto_repr_does_not_expose_key_or_binding(self):
        """Sensitive sort text and context do not appear in nested representations."""
        context = MachinePageContext(
            'synthetic', 1, 'private-scope', 'private-filter', 'generation'
        )
        page = MachinePage(context, (('private-machine-name', 1),), None)
        cursor = PageCursor.start(context, page_limit=1).accept(
            page, requested_cursor=None
        )
        for item in (context, page, cursor, (context, page, cursor)):
            rendered = repr(item)
            for secret in ('private-scope', 'private-filter', 'private-machine-name'):
                with self.subTest(item=type(item).__name__, secret=secret):
                    self.assertNotIn(secret, rendered)

    def test_source_termination_is_not_named_snapshot_completeness(self):
        """A provider-declared terminal page is not completeness evidence."""
        cursor = PageCursor.start(self.context, page_limit=1).accept(
            MachinePage(self.context, (), None), requested_cursor=None
        )
        self.assertFalse(hasattr(cursor, 'complete'))
        self.assertTrue(cursor.provider_terminal)

    def test_malformed_state_containers_fail_safely(self):
        """Malformed carried state cannot bypass limits or cause a type error."""
        for state in (
            PageCursor(self.context, page_limit=1, last_key='not-a-key'),
            PageCursor(self.context, page_limit=1, seen_ids=None),
            PageCursor(self.context, page_limit=1, seen_cursors=None),
            PageCursor(self.context, page_limit=1, expected_cursor=True),
        ):
            with self.subTest(state=state), self.assertRaises(ValueError):
                state.accept(
                    MachinePage(self.context, (('A', 1),), None), requested_cursor=None
                )

    def test_budget_and_terminal_rejections_are_distinct(self):
        """A consumer can distinguish an exhausted budget from a terminal page."""
        from dataclasses import replace

        from assets.integration_machine_pages import MAX_PAGES

        start = PageCursor.start(self.context, page_limit=1)
        exhausted = replace(start, page_count=MAX_PAGES, expected_cursor='next')
        with self.assertRaises(ValueError) as budget:
            exhausted.accept(
                MachinePage(self.context, (), None), requested_cursor='next'
            )
        self.assertEqual(type(budget.exception).__name__, 'PageBudgetExhaustedError')
        terminal = start.accept(
            MachinePage(self.context, (), None), requested_cursor=None
        )
        with self.assertRaises(ValueError) as already:
            terminal.accept(MachinePage(self.context, (), None), requested_cursor=None)
        self.assertEqual(type(already.exception).__name__, 'PageAlreadyTerminalError')
