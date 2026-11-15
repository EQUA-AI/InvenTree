"""Linear traversal controls against an independently seeded synthetic source."""

import copy
import json
import unittest
from dataclasses import replace
from threading import Event, Thread

from assets.tests.test_integration_s2_provider_routing import (
    FixtureSnapshot,
    fixture_binding,
)


class LinearTraversalTests(unittest.TestCase):
    """The caller cannot supply cursor/history or combine state branches."""

    def make(self, rows, *, max_pages=256, cancelled=lambda: False):
        """Use a one-row page to reach actual cross-page boundaries."""
        from assets.integration_s2_provider import PageTraversal

        binding = fixture_binding()
        binding = replace(binding, shape=replace(binding.shape, limit=1))
        source = FixtureSnapshot(binding, rows=rows)
        traversal = PageTraversal(
            binding=binding,
            plan=source.plan(),
            binding_factory=lambda: binding,
            max_pages=max_pages,
            cancelled=cancelled,
        )
        return binding, source, traversal

    def test_two_pages_own_real_cursor_history_and_detached_rows(self):
        """The final page follows the first without fetching/materializing all rows."""
        _, source, traversal = self.make([
            {'pk': 1, 'name': 'A'},
            {'pk': 2, 'name': 'B'},
        ])
        first = traversal.read_next()
        self.assertFalse(first.snapshot_complete)
        self.assertEqual(len(source.calls), 1)
        first.rows[0]['name'] = 'mutated-consumer-copy'
        second = traversal.read_next()
        self.assertTrue(second.snapshot_complete)
        self.assertEqual(second.rows, [{'pk': 2, 'name': 'B'}])
        self.assertEqual(
            source.calls, [(0, 0, None, 'fixture-v1'), (1, 1, 'issued:1', 'fixture-v1')]
        )
        with self.assertRaises(ValueError) as raised:
            traversal.read_next()
        self.assertEqual(raised.exception.code, 'state')
        self.assertEqual(len(source.calls), 2)

    def test_duplicate_identity_across_ordered_pages_is_refused(self):
        """Different names cannot disguise a repeated source primary key."""
        _, _, traversal = self.make([{'pk': 1, 'name': 'A'}, {'pk': 1, 'name': 'B'}])
        traversal.read_next()
        with self.assertRaises(ValueError):
            traversal.read_next()

    def test_cross_page_order_cannot_restart_at_each_page(self):
        """Individually ordered pages are not enough to establish global order."""
        _, _, traversal = self.make([{'pk': 1, 'name': 'B'}, {'pk': 2, 'name': 'A'}])
        traversal.read_next()
        with self.assertRaises(ValueError):
            traversal.read_next()

    def test_terminal_at_budget_succeeds_but_continuation_is_not_complete(self):
        """A bounded scan cannot silently restart its budget to reach the end."""
        rows = [{'pk': 1, 'name': 'A'}, {'pk': 2, 'name': 'B'}]
        _, _, complete = self.make(rows, max_pages=2)
        complete.read_next()
        self.assertTrue(complete.read_next().snapshot_complete)
        _, source, incomplete = self.make([*rows, {'pk': 3, 'name': 'C'}], max_pages=2)
        incomplete.read_next()
        self.assertFalse(incomplete.read_next().snapshot_complete)
        with self.assertRaises(ValueError) as raised:
            incomplete.read_next()
        self.assertEqual(raised.exception.code, 'budget')
        self.assertEqual(len(source.calls), 2)

    def test_failed_traversal_cannot_resume_or_mix_a_retry_branch(self):
        """An ambiguous failed page requires a new traversal, not state reuse."""
        _, source, traversal = self.make([
            {'pk': 1, 'name': 'A'},
            {'pk': 2, 'name': 'B'},
        ])
        traversal.read_next()
        source.transform = lambda page: replace(page, generation='wrong')
        with self.assertRaises(ValueError):
            traversal.read_next()
        source.transform = lambda page: page
        with self.assertRaises(ValueError) as raised:
            traversal.read_next()
        self.assertEqual(raised.exception.code, 'state')
        self.assertEqual(len(source.calls), 2)

    def test_cancellation_before_and_after_provider_read_is_not_success(self):
        """Cooperative cancellation is checked at both sides of source work."""
        cancelled = False
        _, source, traversal = self.make(
            [{'pk': 1, 'name': 'A'}], cancelled=lambda: cancelled
        )

        def after_read(page):
            nonlocal cancelled
            cancelled = True
            return page

        source.transform = after_read
        with self.assertRaises(ValueError) as raised:
            traversal.read_next()
        self.assertEqual(raised.exception.code, 'cancelled')
        self.assertEqual(len(source.calls), 1)
        _, source, traversal = self.make([], cancelled=lambda: True)
        with self.assertRaises(ValueError) as raised:
            traversal.read_next()
        self.assertEqual(raised.exception.code, 'cancelled')
        self.assertEqual(source.calls, [])

    def test_copying_linear_state_is_not_a_supported_cursor_protocol(self):
        """Neither shallow nor deep copying can turn state into a resume token."""
        _, _, traversal = self.make([])
        for operation in (copy.copy, copy.deepcopy):
            with (
                self.subTest(operation=operation.__name__),
                self.assertRaises(TypeError),
            ):
                operation(traversal)

    def test_independent_oracle_rejects_omission_false_counts_and_unissued_tokens(self):
        """A structurally plausible terminal page is not proof of full coverage."""
        from assets.integration_s2_provider import ReadError

        rows = [{'pk': 1, 'name': 'A'}, {'pk': 2, 'name': 'B'}]
        for change in (
            lambda page: replace(page, rows=(rows[1],)),
            lambda page: replace(page, total=1, next_cursor=None),
            lambda page: replace(page, rows=(), total=0, next_cursor=None),
            lambda page: replace(page, next_cursor='not-issued-by-fixture'),
            lambda page: replace(page, position=1),
            lambda page: replace(page, coverage_complete=False),
        ):
            with self.subTest(change=change.__name__):
                _, source, traversal = self.make(rows)
                source.transform = change
                with self.assertRaises(ReadError):
                    traversal.read_next()
                self.assertEqual(len(source.calls), 1)

    def test_current_binding_changes_before_continuation_prevent_another_fetch(self):
        """Each actual next read must retain actor, scope, request and shape."""
        from assets.integration_s2_provider import PageTraversal, ReadAuthorityError

        initial = fixture_binding()
        initial = replace(initial, shape=replace(initial.shape, limit=1))
        changes = (
            replace(initial, actor_pk=2),
            replace(initial, environment='other'),
            replace(initial, client_ids=(11,)),
            replace(initial, request_scope='narrower'),
            replace(
                initial, shape=replace(initial.shape, filters=(('active', 'false'),))
            ),
            replace(initial, shape=replace(initial.shape, ordering='other-order')),
        )
        for changed in changes:
            state = {'current': initial}
            source = FixtureSnapshot(
                initial, rows=[{'pk': 1, 'name': 'A'}, {'pk': 2, 'name': 'B'}]
            )
            traversal = PageTraversal(
                binding=initial,
                plan=source.plan(),
                binding_factory=lambda state=state: state['current'],
            )
            traversal.read_next()
            state['current'] = changed
            with (
                self.subTest(changed=repr(changed)),
                self.assertRaises(ReadAuthorityError),
            ):
                traversal.read_next()
            self.assertEqual(len(source.calls), 1)

    def test_count_change_cannot_be_waived_by_individually_accepted_source_pages(self):
        """Even a faulty proof cannot make changed totals one stable traversal."""
        from assets.integration_s2_provider import ReadIncompleteError

        rows = [{'pk': 1, 'name': 'A'}, {'pk': 2, 'name': 'B'}, {'pk': 3, 'name': 'C'}]
        _, source, traversal = self.make(rows)
        traversal.read_next()
        # Deliberately corrupt the fake oracle itself, not a production proof.
        source.oracle = json.dumps(rows[:2])
        with self.assertRaises(ReadIncompleteError):
            traversal.read_next()

    def test_source_proof_cannot_waive_repeated_continuation_token(self):
        """The owned state checks token history independently of fake proof."""
        from assets.integration_s2_provider import PageTraversal, ReadError

        binding = fixture_binding()
        binding = replace(binding, shape=replace(binding.shape, limit=1))
        source = FixtureSnapshot(
            binding,
            rows=[
                {'pk': 1, 'name': 'A'},
                {'pk': 2, 'name': 'B'},
                {'pk': 3, 'name': 'C'},
            ],
        )
        source.transform = lambda page: replace(
            page, next_cursor='repeated' if page.next_cursor else None
        )
        plan = replace(source.plan(), verify=lambda page: True)
        traversal = PageTraversal(
            binding=binding, plan=plan, binding_factory=lambda: binding
        )
        traversal.read_next()
        with self.assertRaises(ReadError):
            traversal.read_next()
        self.assertEqual(len(source.calls), 2)

    def test_generation_is_pinned_even_if_a_trusted_plan_is_mutated(self):
        """Replacing both returned and advertised generation cannot move the pin."""
        from assets.integration_s2_provider import PageTraversal, ReadError

        binding = fixture_binding()
        source = FixtureSnapshot(binding)
        plan = source.plan()

        def corrupt(page):
            object.__setattr__(plan.capability, 'generation', 'new-generation')
            return replace(page, generation='new-generation')

        source.transform = corrupt
        traversal = PageTraversal(
            binding=binding, plan=plan, binding_factory=lambda: binding
        )
        with self.assertRaises(ReadError):
            traversal.read_next()

    def test_concurrent_reader_is_refused_without_a_second_source_call(self):
        """This is an in-process state control, not a DB atomicity proof."""
        from assets.integration_s2_provider import ReadStateError

        _, source, traversal = self.make([{'pk': 1, 'name': 'A'}])
        entered, release = Event(), Event()
        results, errors = [], []

        def block(page):
            entered.set()
            if not release.wait(5):
                raise TimeoutError()
            return page

        def read():
            try:
                results.append(traversal.read_next())
            except Exception as exc:
                errors.append(exc)

        source.transform = block
        thread = Thread(target=read, name='s2-owned-traversal')
        thread.start()
        try:
            self.assertTrue(entered.wait(5))
            with self.assertRaises(ReadStateError):
                traversal.read_next()
        finally:
            release.set()
            thread.join(timeout=5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertTrue(results[0].snapshot_complete)
        self.assertEqual(len(source.calls), 1)

    def test_the_actual_page_ceiling_has_distinct_terminal_and_incomplete_outcomes(
        self,
    ):
        """Exercise the production ceiling, not only an easier two-page budget."""
        from assets.integration_s2_provider import MAX_PAGES, ReadBudgetError

        for count in (MAX_PAGES, MAX_PAGES + 1):
            rows = [{'pk': index + 1, 'name': f'{index:04}'} for index in range(count)]
            _, source, traversal = self.make(rows)
            for _ in range(MAX_PAGES):
                last = traversal.read_next()
            self.assertEqual(last.snapshot_complete, count == MAX_PAGES)
            self.assertEqual(len(source.calls), MAX_PAGES)
            if count > MAX_PAGES:
                with self.assertRaises(ReadBudgetError):
                    traversal.read_next()
                self.assertEqual(len(source.calls), MAX_PAGES)
