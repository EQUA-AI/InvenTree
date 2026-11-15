"""Offline contract controls for bounded, generation-bound machine pages."""

import unittest


class MachinePageContractTests(unittest.TestCase):
    """Page consistency cannot substitute for source or actor authority."""

    def test_two_ordered_pages_bind_scope_and_generation(self):
        """A valid continuation preserves the original request and ordering."""
        from assets.integration_machine_pages import (
            MachinePage,
            MachinePageContext,
            PageCursor,
        )

        context = MachinePageContext(
            environment='site-a',
            actor_pk=3,
            scope='client-a',
            filters='active-only',
            generation='revision-1',
        )
        first = MachinePage(
            context=context, keys=(('A', 1), ('B', 2)), next_cursor='next-1'
        )
        second = MachinePage(context=context, keys=(('C', 3),), next_cursor=None)
        cursor = PageCursor.start(context, page_limit=2)
        cursor = cursor.accept(first, requested_cursor=None)
        self.assertFalse(cursor.provider_terminal)
        cursor = cursor.accept(second, requested_cursor='next-1')
        self.assertTrue(cursor.provider_terminal)
        self.assertEqual(cursor.last_key, ('C', 3))

    def test_refuses_scope_generation_cursor_and_order_mismatch(self):
        """Do not admit a page that cannot belong to this traversal."""
        from dataclasses import replace

        from assets.integration_machine_pages import (
            MachinePage,
            MachinePageContext,
            PageCursor,
        )

        context = MachinePageContext(
            environment='site-a',
            actor_pk=3,
            scope='client-a',
            filters='active-only',
            generation='revision-1',
        )
        cursor = PageCursor.start(context, page_limit=2).accept(
            MachinePage(context=context, keys=(('A', 1),), next_cursor='next-1'),
            requested_cursor=None,
        )
        for page, requested in (
            (MachinePage(replace(context, actor_pk=4), (('B', 2),), None), 'next-1'),
            (
                MachinePage(replace(context, scope='client-b'), (('B', 2),), None),
                'next-1',
            ),
            (MachinePage(replace(context, filters='all'), (('B', 2),), None), 'next-1'),
            (
                MachinePage(
                    replace(context, generation='revision-2'), (('B', 2),), None
                ),
                'next-1',
            ),
            (MachinePage(context, (('B', 2),), None), 'wrong-cursor'),
            (MachinePage(context, (('A', 1),), None), 'next-1'),
            (MachinePage(context, (('C', 3), ('B', 2)), None), 'next-1'),
        ):
            with (
                self.subTest(page=page, requested=requested),
                self.assertRaises(ValueError),
            ):
                cursor.accept(page, requested_cursor=requested)

    def test_duplicate_identity_malformed_pages_and_cursor_cycles_refused(self):
        """Reject duplicate IDs even when their sort names differ."""
        from assets.integration_machine_pages import (
            MachinePage,
            MachinePageContext,
            PageCursor,
        )

        context = MachinePageContext('site-a', 3, 'client-a', 'all', 'gen-1')
        first = PageCursor.start(context, page_limit=2).accept(
            MachinePage(context, (('A', 1),), 'one'), requested_cursor=None
        )
        second = first.accept(
            MachinePage(context, (('B', 2),), 'two'), requested_cursor='one'
        )
        for page in (
            MachinePage(context, (('Z', 1),), None),
            MachinePage(context, (('C', True),), None),
            MachinePage(context, (('C', 3), ('D', 4), ('E', 5)), None),
            MachinePage(context, (), 'three'),
            MachinePage(context, (('C', 3),), 'one'),
            MachinePage(context, (('C', 3),), 'two'),
        ):
            with self.subTest(page=page), self.assertRaises(ValueError):
                second.accept(page, requested_cursor='two')
        final = second.accept(MachinePage(context, (), None), requested_cursor='two')
        self.assertTrue(final.provider_terminal)
        with self.assertRaises(ValueError):
            final.accept(MachinePage(context, (), None), requested_cursor=None)

    def test_request_coordinates_and_page_bounds_are_literal(self):
        """Malformed coordinates and oversized page limits are refused."""
        from assets.integration_machine_pages import MachinePageContext, PageCursor

        class SpoofedString(str):
            __hash__ = str.__hash__

            def __eq__(self, other):
                return True

        for value in ('', '  ', SpoofedString('site-b'), None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                MachinePageContext(value, 3, 'client-a', 'all', 'gen-1')
        context = MachinePageContext('site-a', 3, 'client-a', 'all', 'gen-1')
        for limit in (True, 0, 101, '2'):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                PageCursor.start(context, page_limit=limit)

    def test_page_budget_exhaustion_is_not_terminal_completion(self):
        """A longer result cannot silently complete at the local budget."""
        from assets.integration_machine_pages import (
            MAX_PAGES,
            MachinePage,
            MachinePageContext,
            PageCursor,
        )

        context = MachinePageContext('site-a', 3, 'client-a', 'all', 'gen-1')
        cursor = PageCursor.start(context, page_limit=1)
        token = None
        for index in range(MAX_PAGES):
            next_token = f'next-{index}'
            cursor = cursor.accept(
                MachinePage(context, ((f'{index:04d}', index + 1),), next_token),
                requested_cursor=token,
            )
            token = next_token
        self.assertFalse(cursor.provider_terminal)
        with self.assertRaises(ValueError):
            cursor.accept(MachinePage(context, (), None), requested_cursor=token)
