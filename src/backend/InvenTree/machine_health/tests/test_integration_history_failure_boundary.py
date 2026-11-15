"""Synthetic failure-boundary controls for unwired scoped history traversal."""

import traceback
from unittest import TestCase

from machine_health.services import integration_history as history


class HistoryAdapterFailureBoundaryTests(TestCase):
    """Failures must not bypass the current authority check."""

    def test_authority_loss_during_unclassified_adapter_failure_denies(self):
        """A revoked source read yields an authorization error, not raw details."""
        context = history.HistoryReadContext('synthetic', 1, 2, 3, 4, 'generation')
        window = history.HistoryWindow(0, 4)
        allowed = [True]
        checks = []

        def authorize(bound, requested):
            checks.append((bound, requested, allowed[0]))
            return allowed[0]

        def read(bound, requested, limit):
            allowed[0] = False
            raise RuntimeError('synthetic-provider-private-detail')

        with self.assertRaises(history.HistoryAuthorizationError) as raised:
            history.traverse_scoped_history(
                context,
                window,
                read_window=read,
                authorize=authorize,
                max_samples_per_read=2,
                max_samples_total=2,
                max_reads=1,
            )
        self.assertEqual([check[2] for check in checks], [True, False])
        self.assertNotIn('synthetic-provider-private-detail', str(raised.exception))

    def test_authorized_unclassified_failure_is_safe_typed_error_not_empty(self):
        """An unknown error remains a failure without exposing its raw message."""
        context = history.HistoryReadContext('synthetic', 1, 2, 3, 4, 'generation')
        window = history.HistoryWindow(0, 4)
        checks = []

        def authorize(bound, requested):
            checks.append((bound, requested))
            return True

        def read(bound, requested, limit):
            raise RuntimeError('synthetic-provider-private-detail')

        with self.assertRaises(Exception) as raised:
            history.traverse_scoped_history(
                context,
                window,
                read_window=read,
                authorize=authorize,
                max_samples_per_read=2,
                max_samples_total=2,
                max_reads=1,
            )
        self.assertEqual(type(raised.exception).__name__, 'HistoryAdapterError')
        self.assertEqual(checks, [(context, window), (context, window)])
        self.assertNotIn('synthetic-provider-private-detail', str(raised.exception))
        formatted = ''.join(traceback.format_exception(raised.exception))
        self.assertNotIn('synthetic-provider-private-detail', formatted)

    def test_process_control_exceptions_are_not_converted(self):
        """Interrupts and exits propagate instead of becoming adapter failures."""
        context = history.HistoryReadContext('synthetic', 1, 2, 3, 4, 'generation')
        window = history.HistoryWindow(0, 4)
        for error in (KeyboardInterrupt(), SystemExit()):
            with self.subTest(error=type(error).__name__):

                def read(bound, requested, limit, raised=error):
                    raise raised

                with self.assertRaises(type(error)):
                    history.traverse_scoped_history(
                        context,
                        window,
                        read_window=read,
                        authorize=lambda bound, requested: True,
                        max_samples_per_read=2,
                        max_samples_total=2,
                        max_reads=1,
                    )

    def test_authority_check_failure_is_not_converted_to_a_result(self):
        """A broken recheck cannot be treated as permission or an empty result."""
        context = history.HistoryReadContext('synthetic', 1, 2, 3, 4, 'generation')
        window = history.HistoryWindow(0, 4)
        checks = iter((True, RuntimeError('synthetic-authority-failure')))

        def authorize(bound, requested):
            outcome = next(checks)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        def read(bound, requested, limit):
            raise RuntimeError('synthetic-provider-private-detail')

        with self.assertRaises(history.HistoryAuthorizationError) as raised:
            history.traverse_scoped_history(
                context,
                window,
                read_window=read,
                authorize=authorize,
                max_samples_per_read=2,
                max_samples_total=2,
                max_reads=1,
            )
        formatted = ''.join(traceback.format_exception(raised.exception))
        self.assertNotIn('synthetic-authority-failure', formatted)
        self.assertNotIn('synthetic-provider-private-detail', formatted)

    def test_callback_failures_are_denied_at_each_authority_boundary(self):
        """Initial, post-read, classified-error and final checks fail closed."""
        context = history.HistoryReadContext('synthetic', 1, 2, 3, 4, 'generation')
        window = history.HistoryWindow(0, 4)
        for failure_check, classified in (
            (1, False),
            (2, False),
            (2, True),
            (3, False),
        ):
            with self.subTest(failure_check=failure_check, classified=classified):
                checks = []
                reads = []

                def authorize(bound, requested, checks=checks, fail=failure_check):
                    checks.append((bound, requested))
                    if len(checks) == fail:
                        raise RuntimeError('synthetic-authority-private-detail')
                    return True

                def read(bound, requested, limit, reads=reads, classified=classified):
                    reads.append(requested)
                    if classified:
                        raise history.HistorySourceUnavailableError('SOURCE_TIMEOUT')
                    return history.HistoryPage('generation', (), False)

                with self.assertRaises(history.HistoryAuthorizationError) as raised:
                    history.traverse_scoped_history(
                        context,
                        window,
                        read_window=read,
                        authorize=authorize,
                        max_samples_per_read=2,
                        max_samples_total=2,
                        max_reads=1,
                    )
                self.assertEqual(len(checks), failure_check)
                self.assertEqual(len(reads), 0 if failure_check == 1 else 1)
                formatted = ''.join(traceback.format_exception(raised.exception))
                self.assertNotIn('synthetic-authority-private-detail', formatted)

    def test_failure_after_prior_complete_window_returns_no_partial_history(self):
        """A later unclassified failure aborts rather than returning prior data."""
        context = history.HistoryReadContext('synthetic', 1, 2, 3, 4, 'generation')
        requested = history.HistoryWindow(0, 4)
        sample = history.HistorySample('first', 1, 1, None, None)
        calls = []

        def read(bound, window, limit):
            calls.append(window)
            if window == requested:
                return history.HistoryPage('generation', (), True)
            if window == history.HistoryWindow(0, 2):
                return history.HistoryPage('generation', (sample,), False)
            raise RuntimeError('synthetic-provider-private-detail')

        with self.assertRaises(history.HistoryAdapterError):
            history.traverse_scoped_history(
                context,
                requested,
                read_window=read,
                authorize=lambda bound, window: True,
                max_samples_per_read=2,
                max_samples_total=4,
                max_reads=3,
            )
        self.assertEqual(
            calls, [requested, history.HistoryWindow(0, 2), history.HistoryWindow(2, 4)]
        )

    def test_invalid_classified_error_does_not_render_adapter_details(self):
        """A corrupted gap classification is a generic protocol failure."""
        context = history.HistoryReadContext('synthetic', 1, 2, 3, 4, 'generation')
        window = history.HistoryWindow(0, 4)

        def read(bound, requested, limit):
            error = history.HistorySourceUnavailableError('SOURCE_TIMEOUT')
            error.reason = 'synthetic-provider-private-detail'
            error.args = ('synthetic-provider-private-detail',)
            raise error

        with self.assertRaises(history.HistoryProtocolError) as raised:
            history.traverse_scoped_history(
                context,
                window,
                read_window=read,
                authorize=lambda bound, requested: True,
                max_samples_per_read=2,
                max_samples_total=2,
                max_reads=1,
            )
        formatted = ''.join(traceback.format_exception(raised.exception))
        self.assertNotIn('synthetic-provider-private-detail', formatted)
