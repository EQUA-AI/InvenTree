"""Synthetic contract tests for bounded, source-proven history traversal."""

import random
import unittest
from dataclasses import FrozenInstanceError
from itertools import pairwise

from machine_health.services import integration_history as history


class RecordedHistoryTraversalTests(unittest.TestCase):
    """A fake adapter supplies source identity and completeness evidence."""

    def test_scoped_callbacks_receive_fixed_actor_source_and_requested_window(self):
        """The adapter and authorization check share one explicit read context."""
        reader = history.traverse_scoped_history
        context = history.HistoryReadContext(
            environment='test',
            actor_pk=1,
            machine_pk=2,
            binding_pk=3,
            source_pk=4,
            generation='snapshot-1',
        )
        requested = history.HistoryWindow(0, 4)
        sample = history.HistorySample('sample-1', 1, 7, None, None)
        current_actor = [1]
        checks = []
        reads = []

        def authorize(bound, full_window):
            checks.append((bound, full_window))
            return bound.actor_pk == current_actor[0]

        def read(bound, window, limit):
            reads.append((bound, window, limit))
            return history.HistoryPage(bound.generation, (sample,), False)

        result = reader(
            context,
            requested,
            read_window=read,
            authorize=authorize,
            max_samples_per_read=2,
            max_samples_total=4,
            max_reads=4,
        )
        self.assertEqual(result.samples, (sample,))
        self.assertEqual(reads, [(context, requested, 2)])
        self.assertEqual(checks, [(context, requested)] * 3)

        def changed_actor(bound, window, limit):
            current_actor[0] = 2
            return history.HistoryPage(bound.generation, (sample,), False)

        with self.assertRaises(history.HistoryAuthorizationError):
            reader(
                context,
                requested,
                read_window=changed_actor,
                authorize=authorize,
                max_samples_per_read=2,
                max_samples_total=4,
                max_reads=4,
            )

    def test_scoped_context_rejects_invalid_ids_and_is_immutable(self):
        """Untrusted malformed identifiers cannot silently become a context."""
        valid = {
            'environment': 'test',
            'actor_pk': 1,
            'machine_pk': 2,
            'binding_pk': 3,
            'source_pk': 4,
            'generation': 'snapshot-1',
        }
        for field, value in (
            ('environment', ' '),
            ('generation', ''),
            ('actor_pk', True),
            ('machine_pk', 0),
            ('binding_pk', '3'),
            ('source_pk', -1),
        ):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                history.HistoryReadContext(**{**valid, field: value})
        context = history.HistoryReadContext(**valid)
        with self.assertRaises(FrozenInstanceError):
            context.__setattr__('actor_pk', 9)

    def test_scoped_generation_change_aborts_instead_of_returning_samples(self):
        """A fake adapter cannot switch to a different declared generation."""
        context = history.HistoryReadContext('test', 1, 2, 3, 4, 'snapshot-1')
        with self.assertRaises(history.HistoryProtocolError):
            history.traverse_scoped_history(
                context,
                history.HistoryWindow(0, 4),
                read_window=lambda c, w, n: history.HistoryPage(
                    'snapshot-2', (), False
                ),
                authorize=lambda c, w: True,
                max_samples_per_read=2,
                max_samples_total=4,
                max_reads=4,
            )

    def test_scoped_context_rejects_spoofed_generation(self):
        """An overridable string cannot identify the trusted source generation."""

        class SpoofedGeneration(str):
            def __ne__(self, other):
                return False

        with self.subTest(boundary='context'), self.assertRaises(ValueError):
            history.HistoryReadContext(
                'test', 1, 2, 3, 4, SpoofedGeneration('snapshot-1')
            )
        with self.subTest(boundary='traversal'), self.assertRaises(ValueError):
            self._traverse(
                lambda w, n: history.HistoryPage('snapshot-2', (), False),
                expected_generation=SpoofedGeneration('snapshot-1'),
            )

    def test_scoped_page_rejects_spoofed_generation(self):
        """A source cannot disguise a different generation with custom equality."""

        class SpoofedGeneration(str):
            def __ne__(self, other):
                return False

        context = history.HistoryReadContext('test', 1, 2, 3, 4, 'snapshot-1')
        sample = history.HistorySample('sample-1', 1, 7, None, None)

        def traverse(generation):
            return history.traverse_scoped_history(
                context,
                history.HistoryWindow(0, 4),
                read_window=lambda c, w, n: history.HistoryPage(
                    generation, (sample,), False
                ),
                authorize=lambda c, w: True,
                max_samples_per_read=2,
                max_samples_total=4,
                max_reads=4,
            )

        self.assertEqual(traverse('snapshot-1').samples, (sample,))
        with self.assertRaises(history.HistoryProtocolError):
            traverse(SpoofedGeneration('snapshot-2'))

    def test_duplicate_sample_identity_cannot_spoof_comparison(self):
        """A source ID subclass cannot hide a repeated logical sample ID."""

        class SpoofedIdentity(str):
            __hash__ = str.__hash__

            def __eq__(self, other):
                return False

        first = history.HistorySample('sample-1', 1, 7, None, None)
        unique = history.HistorySample('sample-2', 2, 8, None, None)
        repeated = history.HistorySample(SpoofedIdentity('sample-1'), 2, 8, None, None)

        def traverse(samples):
            return self._traverse(
                lambda w, n: history.HistoryPage('snapshot-1', samples, False)
            )

        self.assertEqual(traverse((first, unique)).samples, (first, unique))
        with self.assertRaises(history.HistoryProtocolError):
            traverse((first, repeated))

    def test_exact_cap_window_is_complete_and_preserves_samples(self):
        """An exactly full, source-proven page is not implicitly truncated."""
        window = history.HistoryWindow(0, 10)
        samples = (
            history.HistorySample('synthetic-1', 1, 1.5, 'good', 'bar'),
            history.HistorySample('synthetic-2', 4, 2.0, None, 'bar'),
        )
        calls = []

        def read_window(requested, limit):
            calls.append((requested, limit))
            return history.HistoryPage('snapshot-1', samples, truncated=False)

        result = history._traverse_history(
            window,
            read_window=read_window,
            authorize=lambda: True,
            expected_generation='snapshot-1',
            max_samples_per_read=2,
            max_samples_total=4,
            max_reads=4,
        )
        self.assertEqual(result.samples, samples)
        self.assertEqual(result.gaps, ())
        self.assertTrue(result.complete)
        self.assertEqual(calls, [(window, 2)])

    def _traverse(self, read_window, **overrides):
        options = {
            'authorize': lambda: True,
            'expected_generation': 'snapshot-1',
            'max_samples_per_read': 2,
            'max_samples_total': 10,
            'max_reads': 16,
        }
        options.update(overrides)
        return history._traverse_history(
            history.HistoryWindow(0, 4), read_window=read_window, **options
        )

    def test_overfull_equal_timestamp_reports_gap_without_skipping_tie(self):
        """A minimum-resolution truncated interval stays incomplete, not empty."""
        samples = (
            history.HistorySample('a', 1, 1, 'good', 'bar'),
            history.HistorySample('b', 1, 2, 'good', 'bar'),
            history.HistorySample('c', 1, 3, 'bad', 'bar'),
            history.HistorySample('d', 3, 4, None, None),
        )
        calls = []

        def read(window, limit):
            calls.append(window)
            selected = tuple(
                sample
                for sample in samples
                if window.start_ms <= sample.observed_ms < window.end_ms
            )
            return history.HistoryPage(
                'snapshot-1', selected[:limit], len(selected) > limit
            )

        result = self._traverse(read)
        self.assertEqual(result.samples, (samples[3],))
        self.assertEqual(
            result.gaps,
            (
                history.HistoryGap(
                    history.HistoryWindow(1, 2), 'UNRESOLVED_TIMESTAMP_TIE'
                ),
            ),
        )
        self.assertFalse(result.complete)
        self.assertEqual(len(calls), 5)

    def test_split_complete_windows_preserve_order_without_duplicates(self):
        """Discard truncated parents and emit each proven child exactly once."""
        samples = tuple(
            history.HistorySample(str(i), i, i, 'good', None) for i in range(4)
        )

        def read(window, limit):
            selected = tuple(
                s for s in samples if window.start_ms <= s.observed_ms < window.end_ms
            )
            return history.HistoryPage(
                'snapshot-1', selected[:limit], len(selected) > limit
            )

        result = self._traverse(read)
        self.assertEqual(result.samples, samples)
        self.assertTrue(result.complete)

    def test_read_and_sample_budget_exhaustion_leave_explicit_intervals(self):
        """Exhaustion does not falsely certify unvisited or unreturned data."""

        def read(window, limit):
            selected = tuple(
                history.HistorySample(str(i), i, i, None, None)
                for i in range(window.start_ms, window.end_ms)
            )
            return history.HistoryPage(
                'snapshot-1', selected[:limit], len(selected) > limit
            )

        result = self._traverse(read, max_reads=1)
        self.assertEqual(
            result.gaps,
            (
                history.HistoryGap(
                    history.HistoryWindow(0, 2), 'READ_BUDGET_EXHAUSTED'
                ),
                history.HistoryGap(
                    history.HistoryWindow(2, 4), 'READ_BUDGET_EXHAUSTED'
                ),
            ),
        )
        self.assertEqual(result.samples, ())
        result = self._traverse(read, max_samples_total=2)
        self.assertEqual([s.observed_ms for s in result.samples], [0, 1])
        self.assertEqual(
            result.gaps,
            (
                history.HistoryGap(
                    history.HistoryWindow(2, 4), 'SAMPLE_BUDGET_EXHAUSTED'
                ),
            ),
        )

    def test_sample_budget_splits_complete_page_to_preserve_available_prefix(self):
        """An oversized complete page can yield a proven bounded subwindow."""
        samples = tuple(
            history.HistorySample(str(i), i, i, None, None) for i in range(4)
        )

        def read(window, limit):
            selected = tuple(
                sample
                for sample in samples
                if window.start_ms <= sample.observed_ms < window.end_ms
            )
            return history.HistoryPage('snapshot-1', selected, False)

        result = self._traverse(read, max_samples_per_read=4, max_samples_total=2)
        self.assertEqual(result.samples, samples[:2])
        self.assertEqual(
            result.gaps,
            (
                history.HistoryGap(
                    history.HistoryWindow(2, 4), 'SAMPLE_BUDGET_EXHAUSTED'
                ),
            ),
        )

    def test_sample_budget_does_not_return_partial_equal_time_interval(self):
        """A complete tie larger than remaining budget becomes an explicit gap."""
        samples = tuple(
            history.HistorySample(str(i), 1, i, None, None) for i in range(3)
        )

        def read(window, limit):
            selected = tuple(
                sample
                for sample in samples
                if window.start_ms <= sample.observed_ms < window.end_ms
            )
            return history.HistoryPage('snapshot-1', selected, False)

        result = self._traverse(read, max_samples_per_read=4, max_samples_total=2)
        self.assertEqual(result.samples, ())
        self.assertEqual(
            result.gaps,
            (
                history.HistoryGap(
                    history.HistoryWindow(1, 2), 'SAMPLE_BUDGET_EXHAUSTED'
                ),
            ),
        )

    def test_unavailable_and_source_proven_empty_are_distinct(self):
        """Unavailable is a gap; a source-proven empty window is complete."""

        def unavailable(window, limit):
            raise history.HistorySourceUnavailableError('SOURCE_TIMEOUT')

        missing = self._traverse(unavailable)
        self.assertEqual(missing.samples, ())
        self.assertEqual(missing.gaps[0].reason, 'SOURCE_TIMEOUT')
        self.assertFalse(missing.complete)
        empty = self._traverse(
            lambda w, n: history.HistoryPage('snapshot-1', (), False)
        )
        self.assertEqual(empty.samples, ())
        self.assertTrue(empty.complete)

    def test_bounded_retry_recovers_available_half_of_timeout_window(self):
        """An explicitly permitted split preserves available sub-history."""
        sample = history.HistorySample('available', 3, 1, None, None)

        def read(window, limit):
            if window.start_ms < 2:
                raise history.HistorySourceUnavailableError('SOURCE_TIMEOUT')
            return history.HistoryPage('snapshot-1', (sample,), False)

        result = self._traverse(read, split_recoverable_errors=True)
        self.assertEqual(result.samples, (sample,))
        self.assertEqual(
            result.gaps,
            (
                history.HistoryGap(history.HistoryWindow(0, 1), 'SOURCE_TIMEOUT'),
                history.HistoryGap(history.HistoryWindow(1, 2), 'SOURCE_TIMEOUT'),
            ),
        )
        self.assertFalse(result.complete)

    def test_retry_opt_in_does_not_retry_global_unavailability(self):
        """Global source failures remain one gap even when splitting is enabled."""
        calls = []

        def read(window, limit):
            calls.append(window)
            raise history.HistorySourceUnavailableError('SOURCE_UNAVAILABLE')

        result = self._traverse(read, split_recoverable_errors=True)
        self.assertEqual(calls, [history.HistoryWindow(0, 4)])
        self.assertEqual(
            result.gaps,
            (history.HistoryGap(history.HistoryWindow(0, 4), 'SOURCE_UNAVAILABLE'),),
        )

    def test_retry_respects_read_budget_and_rechecks_failed_read_authority(self):
        """A timeout split cannot exceed the read budget or expose denied output."""
        calls = []

        def read(window, limit):
            calls.append(window)
            raise history.HistorySourceUnavailableError('SOURCE_TIMEOUT')

        result = self._traverse(read, max_reads=1, split_recoverable_errors=True)
        self.assertEqual(len(calls), 1)
        self.assertEqual(
            result.gaps,
            (
                history.HistoryGap(
                    history.HistoryWindow(0, 2), 'READ_BUDGET_EXHAUSTED'
                ),
                history.HistoryGap(
                    history.HistoryWindow(2, 4), 'READ_BUDGET_EXHAUSTED'
                ),
            ),
        )
        decisions = iter([True, False])
        with self.assertRaises(history.HistoryAuthorizationError):
            self._traverse(
                read, authorize=lambda: next(decisions), split_recoverable_errors=True
            )

    def test_malformed_source_gap_reason_is_a_protocol_error(self):
        """A mutable exception cannot inject an invalid gap reason."""

        def read(window, limit):
            error = history.HistorySourceUnavailableError('SOURCE_TIMEOUT')
            error.reason = []
            raise error

        with self.assertRaises(history.HistoryProtocolError):
            self._traverse(read)

    def test_authorization_is_checked_after_source_and_loss_aborts_all(self):
        """A revocation after the read raises rather than returning samples."""
        decisions = iter([True, False])
        with self.assertRaises(history.HistoryAuthorizationError):
            self._traverse(
                lambda w, n: history.HistoryPage('snapshot-1', (), False),
                authorize=lambda: next(decisions),
            )

    def test_authorization_is_rechecked_before_final_result(self):
        """A detected post-read revocation withholds all accepted samples."""
        decisions = iter([True, True, False])
        sample = history.HistorySample('synthetic', 1, 1, None, None)
        with self.assertRaises(history.HistoryAuthorizationError):
            self._traverse(
                lambda w, n: history.HistoryPage('snapshot-1', (sample,), False),
                authorize=lambda: next(decisions),
            )

    def test_authorization_loss_after_partial_progress_discards_prior_samples(self):
        """A later revoked check cannot return an earlier successful interval."""
        decisions = iter([True, True, True, True, True, False])

        def read(window, limit):
            if window == history.HistoryWindow(0, 4):
                return history.HistoryPage('snapshot-1', (), True)
            sample = history.HistorySample(
                str(window.start_ms), window.start_ms, 1, None, None
            )
            return history.HistoryPage('snapshot-1', (sample,), False)

        with self.assertRaises(history.HistoryAuthorizationError):
            self._traverse(read, authorize=lambda: next(decisions))

    def test_duplicate_identity_across_complete_intervals_is_rejected(self):
        """A repeated source identity cannot be silently deduplicated."""

        def read(window, limit):
            if window == history.HistoryWindow(0, 4):
                return history.HistoryPage('snapshot-1', (), True)
            sample = history.HistorySample('same-id', window.start_ms, 1, None, None)
            return history.HistoryPage('snapshot-1', (sample,), False)

        with self.assertRaises(history.HistoryProtocolError):
            self._traverse(read)

    def test_generation_change_after_complete_interval_aborts_all(self):
        """A generation mismatch after partial progress is still a hard error."""

        def read(window, limit):
            if window == history.HistoryWindow(0, 4):
                return history.HistoryPage('snapshot-1', (), True)
            sample = history.HistorySample(
                str(window.start_ms), window.start_ms, 1, None, None
            )
            generation = 'snapshot-1' if window.start_ms == 0 else 'snapshot-2'
            return history.HistoryPage(generation, (sample,), False)

        with self.assertRaises(history.HistoryProtocolError):
            self._traverse(read)

    def test_seeded_dense_windows_never_hide_a_sample_outside_a_gap(self):
        """Check synthetic coverage and gap isolation over varied density."""
        rng = random.Random(416)
        for case in range(100):
            samples = tuple(
                history.HistorySample(
                    f'{moment:02}-{index:02}', moment, index, None, None
                )
                for moment in range(16)
                for index in range(rng.randrange(6))
            )

            def read(window, limit, records=samples):
                selected = tuple(
                    sample
                    for sample in records
                    if window.start_ms <= sample.observed_ms < window.end_ms
                )
                return history.HistoryPage(
                    'snapshot-1', selected[:limit], len(selected) > limit
                )

            with self.subTest(case=case):
                result = history._traverse_history(
                    history.HistoryWindow(0, 16),
                    read_window=read,
                    authorize=lambda: True,
                    expected_generation='snapshot-1',
                    max_samples_per_read=3,
                    max_samples_total=100,
                    max_reads=128,
                )
                returned = {sample.source_id for sample in result.samples}
                self.assertEqual(len(returned), len(result.samples))
                for sample in samples:
                    in_gap = any(
                        gap.window.start_ms <= sample.observed_ms < gap.window.end_ms
                        for gap in result.gaps
                    )
                    self.assertNotEqual(sample.source_id in returned, in_gap)
                for left, right in pairwise(result.gaps):
                    self.assertLessEqual(left.window.end_ms, right.window.start_ms)

    def test_mixed_generation_and_invalid_pages_abort_instead_of_partial_success(self):
        """No data is returned when a response violates the source contract."""
        bad = (
            history.HistoryPage('different', (), False),
            history.HistoryPage('snapshot-1', (), 1),
            history.HistoryPage(
                'snapshot-1', [history.HistorySample('a', 1, 1, None, None)], False
            ),
            history.HistoryPage(
                'snapshot-1', (history.HistorySample('a', 4, 1, None, None),), False
            ),
            history.HistoryPage(
                'snapshot-1',
                (
                    history.HistorySample('b', 2, 1, None, None),
                    history.HistorySample('a', 1, 1, None, None),
                ),
                False,
            ),
        )
        for page in bad:
            with (
                self.subTest(page=page),
                self.assertRaises(history.HistoryProtocolError),
            ):
                self._traverse(lambda w, n, result=page: result)

    def test_invalid_ranges_and_budgets_are_rejected_before_source_access(self):
        """Do not coerce booleans or allow unbounded traversal parameters."""
        for start, end in ((True, 2), (1, 1), (3, 2), (0, 1.0)):
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                history.HistoryWindow(start, end)
        for options in (
            {'max_reads': True},
            {'max_reads': 0},
            {'max_samples_per_read': history.MAX_HISTORY_SAMPLES_PER_READ + 1},
            {'max_samples_total': history.MAX_HISTORY_SAMPLES_TOTAL + 1},
            {'expected_generation': ' '},
        ):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self._traverse(
                    lambda w, n: self.fail('Source must not be called'), **options
                )
