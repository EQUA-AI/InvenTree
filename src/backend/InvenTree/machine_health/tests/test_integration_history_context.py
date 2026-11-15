"""Synthetic scoped-history result context binding controls."""

from unittest import TestCase

from machine_health.services import integration_history as history


class ScopedHistoryResultTests(TestCase):
    """A traversal result retains its trusted invocation coordinates."""

    def test_scoped_traversal_returns_the_invocation_context_and_window(self):
        """Results carry coordinates, which still require independent checks."""
        context = history.HistoryReadContext('synthetic', 1, 2, 3, 4, 'gen-1')
        window = history.HistoryWindow(0, 10)
        sample = history.HistorySample('sample', 2, 42, None, None)
        result = history.traverse_scoped_history(
            context,
            window,
            read_window=lambda c, w, n: history.HistoryPage(
                c.generation, (sample,), False
            ),
            authorize=lambda c, w: c == context and w == window,
            max_samples_per_read=2,
            max_samples_total=2,
            max_reads=1,
        )
        self.assertIs(result.context, context)
        self.assertIs(result.requested_window, window)
        self.assertEqual(result.samples, (sample,))
        self.assertEqual(result.gaps, ())
        self.assertNotIn('gen-1', repr(result))

    def test_gap_result_retains_the_same_context_and_requested_window(self):
        """A source failure is still an explicit gap tied to the invocation."""
        context = history.HistoryReadContext('synthetic', 1, 2, 3, 4, 'gen-1')
        window = history.HistoryWindow(0, 10)

        def unavailable(_context, _window, _limit):
            raise history.HistorySourceUnavailableError('SOURCE_UNAVAILABLE')

        result = history.traverse_scoped_history(
            context,
            window,
            read_window=unavailable,
            authorize=lambda c, w: c == context and w == window,
            max_samples_per_read=2,
            max_samples_total=2,
            max_reads=1,
        )
        self.assertIs(result.context, context)
        self.assertIs(result.requested_window, window)
        self.assertEqual(
            result.gaps, (history.HistoryGap(window, 'SOURCE_UNAVAILABLE'),)
        )
        self.assertFalse(result.complete)

    def test_unscoped_result_does_not_invent_context(self):
        """Direct result construction has no inferred authority or coordinates."""
        result = history.HistoryResult((), ())
        self.assertIsNone(result.context)
        self.assertIsNone(result.requested_window)

    def test_context_and_window_must_be_valid_and_present_together(self):
        """Partial or mistyped provenance cannot masquerade as scoped output."""
        context = history.HistoryReadContext('synthetic', 1, 2, 3, 4, 'gen-1')
        window = history.HistoryWindow(0, 10)
        for provided_context, provided_window in (
            (context, None),
            (None, window),
            ('context', window),
            (context, (0, 10)),
        ):
            with (
                self.subTest(context=provided_context, window=provided_window),
                self.assertRaises(ValueError),
            ):
                history.HistoryResult(
                    (), (), context=provided_context, requested_window=provided_window
                )
