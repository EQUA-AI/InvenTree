"""M2 §8.3: compaction_soak_report computes every threshold from the ledger."""

import json
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from aichat.management.commands.compaction_soak_report import build_report, percentile
from aichat.models import ChatCompactionEvent, ChatThread


class _ReportMixin:
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username='soak-compaction')
        cls.threads = [
            ChatThread.objects.create(
                owner=cls.user, scope_key='k', scope_hash='h', namespace='unscoped'
            )
            for _ in range(3)
        ]

    def _event(self, thread, outcome, *, age=timedelta(0), **fields):
        event = ChatCompactionEvent.objects.create(
            thread=thread, outcome=outcome, **fields
        )
        if age:
            ChatCompactionEvent.objects.filter(pk=event.pk).update(
                started_at=timezone.now() - age
            )
        return event

    def _run(self, *args):
        out = StringIO()
        code = 0
        try:
            call_command('compaction_soak_report', *args, stdout=out)
        except SystemExit as exc:
            code = int(exc.code or 0)
        return code, out.getvalue()


class PercentileTest(TestCase):
    """Nearest-rank percentile over the latency samples."""

    def test_nearest_rank(self):
        self.assertIsNone(percentile([], 95))
        self.assertEqual(percentile([5], 95), 5)
        values = list(range(1, 101))
        self.assertEqual(percentile(values, 50), 50)
        self.assertEqual(percentile(values, 95), 95)


class SoakReportMetricsTest(_ReportMixin, TestCase):
    """Fixture rows drive every metric, threshold and the verdict."""

    def test_no_data_exits_2(self):
        code, output = self._run()
        self.assertEqual(code, 2)
        self.assertIn('no_data', output)

    def test_started_only_rows_are_no_data(self):
        self._event(self.threads[0], 'started')
        code, _ = self._run()
        self.assertEqual(code, 2)

    def test_passing_window_exits_0_with_every_metric(self):
        for i in range(10):
            self._event(
                self.threads[i % 2],
                'ok',
                latency_ms=1000 * (i + 1),
                input_tokens=100,
                output_tokens=10,
            )
        # A fresh started row (not yet stale) does not count against the gate.
        self._event(self.threads[2], 'started', age=timedelta(minutes=5))
        # Outside the window: ignored entirely.
        self._event(self.threads[2], 'failed', age=timedelta(days=9))

        code, output = self._run('--days', '7')
        self.assertEqual(code, 0)
        self.assertIn('PASS', output)
        report = build_report(7)
        self.assertEqual(report['events_total'], 11)
        self.assertEqual(report['terminal_total'], 10)
        self.assertEqual(report['outcomes']['ok'], 10)
        self.assertEqual(report['outcomes']['failed'], 0)
        self.assertEqual(report['outcomes']['started'], 1)
        self.assertEqual(report['failure_rate'], 0.0)
        self.assertEqual(report['content_filter_stuck'], 0)
        self.assertEqual(report['cap_hit_rate'], 0.0)
        self.assertEqual(report['race_rate'], 0.0)
        self.assertEqual(report['started_without_terminal'], 0)
        self.assertEqual(report['latency_p50_ms'], 5000)
        self.assertEqual(report['latency_p95_ms'], 10000)
        self.assertEqual(report['input_tokens_total'], 1000)
        self.assertEqual(report['output_tokens_total'], 100)
        self.assertEqual(report['verdict'], 'PASS')
        self.assertTrue(all(t['pass'] for t in report['thresholds'].values()))

    def test_every_threshold_can_fail(self):
        t0, t1, t2 = self.threads
        for _ in range(4):
            self._event(t0, 'ok', latency_ms=70_000)
        self._event(t0, 'failed', error_code='RuntimeError')
        self._event(t1, 'race_lost')
        self._event(t1, 'ok', cap_hit=True, latency_ms=100)
        # t2: two most recent terminal events are both content_filter → stuck.
        self._event(t2, 'ok', latency_ms=100)
        self._event(t2, 'content_filter', error_code='content_filter')
        self._event(t2, 'content_filter', error_code='content_filter')
        self._event(t2, 'started', age=timedelta(minutes=20))

        code, output = self._run()
        self.assertEqual(code, 1)
        self.assertIn('FAIL', output)
        report = build_report(7)
        self.assertEqual(report['terminal_total'], 10)
        self.assertEqual(report['failure_rate'], 0.1)
        self.assertEqual(report['content_filter_stuck'], 1)
        self.assertEqual(report['cap_hit_rate'], 0.1)
        self.assertEqual(report['race_rate'], 0.1)
        self.assertEqual(report['started_without_terminal'], 1)
        self.assertEqual(report['latency_p95_ms'], 70_000)
        self.assertEqual(report['verdict'], 'FAIL')
        for name in (
            'failure_rate',
            'content_filter_stuck',
            'cap_hit_rate',
            'race_rate',
            'started_without_terminal',
            'latency_p95_ms',
        ):
            self.assertFalse(report['thresholds'][name]['pass'], name)

    def test_stuck_needs_two_consecutive_filters(self):
        t0 = self.threads[0]
        self._event(t0, 'content_filter')
        self._event(t0, 'ok', latency_ms=10)
        self._event(t0, 'content_filter')
        self.assertEqual(build_report(7)['content_filter_stuck'], 0)
        self._event(t0, 'content_filter')
        self.assertEqual(build_report(7)['content_filter_stuck'], 1)

    def test_single_failure_can_fail_the_verdict(self):
        self._event(self.threads[0], 'failed')
        code, _ = self._run()
        self.assertEqual(code, 1)

    def test_flags_off_skips_never_count_as_failures(self):
        """The §8.7 posture row sits outside every rate; counted separately."""
        for i in range(10):
            self._event(self.threads[i % 2], 'ok', latency_ms=100)
        self._event(
            self.threads[2], 'skipped', error_code='flags_off', flag_state='off'
        )
        # One filtered batch pins the denominator: with the skip counted as
        # terminal it would read 12, diluting every rate.
        self._event(self.threads[2], 'content_filter', error_code='content_filter')

        code, output = self._run('--json')
        self.assertEqual(code, 0)
        report = json.loads(output)
        self.assertEqual(report['verdict'], 'PASS')
        self.assertEqual(report['events_total'], 12)
        self.assertEqual(report['terminal_total'], 11)
        self.assertEqual(report['skipped_total'], 1)
        self.assertEqual(report['flags_off'], 1)
        self.assertEqual(report['outcomes']['skipped'], 1)
        self.assertEqual(report['outcomes']['failed'], 0)
        self.assertEqual(report['outcomes']['content_filter'], 1)
        self.assertEqual(report['failure_rate'], 0.0)
        self.assertEqual(report['race_rate'], 0.0)
        self.assertEqual(report['cap_hit_rate'], 0.0)
        self.assertTrue(report['thresholds']['failure_rate']['pass'])

        code, text = self._run()
        self.assertEqual(code, 0)
        self.assertIn('skipped_total            = 1', text)
        self.assertIn('flags_off                = 1', text)
        self.assertIn('failure_rate             = 0.0', text)

    def test_budget_deferred_is_a_posture_row_not_a_run(self):
        """§8.4: a daily-cap deferral is counted but sits outside every rate."""
        for i in range(10):
            self._event(self.threads[i % 2], 'ok', latency_ms=100)
        self._event(
            self.threads[2],
            'budget_deferred',
            error_code='daily_cap',
            input_tokens=7,
            output_tokens=3,
        )

        code, output = self._run('--json')
        self.assertEqual(code, 0)
        report = json.loads(output)
        self.assertEqual(report['verdict'], 'PASS')
        self.assertEqual(report['events_total'], 11)
        self.assertEqual(report['terminal_total'], 10)
        self.assertEqual(report['budget_deferred'], 1)
        self.assertEqual(report['outcomes']['budget_deferred'], 1)
        self.assertEqual(report['outcomes']['failed'], 0)
        self.assertEqual(report['failure_rate'], 0.0)
        self.assertEqual(report['race_rate'], 0.0)
        self.assertEqual(report['cap_hit_rate'], 0.0)
        # Nothing from a run that never called the model reaches the totals.
        self.assertEqual(report['latency_samples'], 10)
        self.assertEqual(report['input_tokens_total'], 0)
        self.assertEqual(report['output_tokens_total'], 0)

        code, text = self._run()
        self.assertEqual(code, 0)
        self.assertIn('budget_deferred          = 1', text)
        self.assertIn('terminal_total           = 10', text)
        self.assertIn('failure_rate             = 0.0', text)

    def test_budget_deferred_rows_do_not_dilute_the_rates(self):
        """A capped day mints one deferral per turn; they must not mask failures."""
        t0, t1, _ = self.threads
        for _ in range(4):
            self._event(t0, 'ok', latency_ms=100)
        self._event(t0, 'failed', error_code='RuntimeError')
        self._event(t1, 'race_lost')
        self._event(t1, 'ok', cap_hit=True, latency_ms=100)
        for _ in range(200):
            self._event(t1, 'budget_deferred', error_code='daily_cap')

        code, output = self._run('--json')
        self.assertEqual(code, 1)
        report = json.loads(output)
        self.assertEqual(report['verdict'], 'FAIL')
        self.assertEqual(report['events_total'], 207)
        self.assertEqual(report['terminal_total'], 7)
        self.assertEqual(report['budget_deferred'], 200)
        # 1 / 7, not 1 / 207.
        self.assertEqual(report['failure_rate'], round(1 / 7, 4))
        self.assertEqual(report['race_rate'], round(1 / 7, 4))
        self.assertEqual(report['cap_hit_rate'], round(1 / 7, 4))
        self.assertFalse(report['thresholds']['failure_rate']['pass'])
        self.assertFalse(report['thresholds']['race_rate']['pass'])
        self.assertFalse(report['thresholds']['cap_hit_rate']['pass'])

    def test_budget_deferred_does_not_reset_content_filter_streak(self):
        """A deferral advances no watermark, so it is invisible to the stuck read."""
        t0 = self.threads[0]
        self._event(t0, 'ok', latency_ms=100)
        self._event(t0, 'content_filter', error_code='content_filter')
        self._event(t0, 'budget_deferred', error_code='daily_cap')
        self._event(t0, 'content_filter', error_code='content_filter')

        report = build_report(7)
        self.assertEqual(report['content_filter_stuck'], 1)
        self.assertEqual(report['verdict'], 'FAIL')

    def test_budget_deferred_alone_is_no_data(self):
        """A fully capped window never ran the summarizer: no soak read."""
        self._event(self.threads[0], 'budget_deferred', error_code='daily_cap')
        code, output = self._run()
        self.assertEqual(code, 2)
        self.assertIn('no_data', output)
        self.assertIn('budget_deferred          = 1', output)
        self.assertIn('terminal_total           = 0', output)

        report = build_report(7)
        self.assertEqual(report['verdict'], 'no_data')
        self.assertEqual(report['events_total'], 1)
        self.assertEqual(report['terminal_total'], 0)
        self.assertIsNone(report['failure_rate'])

    def test_skips_alone_are_no_data(self):
        """Flags off on every worker is a posture problem, not a soak read."""
        self._event(self.threads[0], 'skipped', error_code='flags_off')
        code, output = self._run()
        self.assertEqual(code, 2)
        self.assertIn('no_data', output)
        self.assertIn('flags_off                = 1', output)


class SoakReportOutputShapeTest(_ReportMixin, TestCase):
    """JSON shape, key=value text idiom, and no thread id in either."""

    def test_json_shape(self):
        self._event(self.threads[0], 'ok', latency_ms=250, input_tokens=5)
        self._event(self.threads[1], 'content_filter', error_code='content_filter')
        code, output = self._run('--json', '--days', '3')
        self.assertEqual(code, 0)
        report = json.loads(output)
        self.assertEqual(report['window_days'], 3)
        self.assertIn('generated_at', report)
        for key in (
            'events_total',
            'terminal_total',
            'skipped_total',
            'flags_off',
            'budget_deferred',
            'outcomes',
            'failure_rate',
            'content_filter_stuck',
            'cap_hit_rate',
            'race_rate',
            'started_without_terminal',
            'latency_p50_ms',
            'latency_p95_ms',
            'input_tokens_total',
            'output_tokens_total',
            'thresholds',
            'verdict',
        ):
            self.assertIn(key, report)
        self.assertEqual(
            set(report['outcomes']),
            {
                'started',
                'ok',
                'failed',
                'skipped',
                'content_filter',
                'cap_hit',
                'race_lost',
                'budget_deferred',
            },
        )
        self.assertEqual(
            set(report['thresholds']),
            {
                'failure_rate',
                'content_filter_stuck',
                'cap_hit_rate',
                'race_rate',
                'started_without_terminal',
                'latency_p95_ms',
            },
        )
        self.assertEqual(report['verdict'], 'PASS')
        for thread in self.threads:
            self.assertNotIn(thread.pk, output)

    def test_text_output_is_key_value_and_thread_free(self):
        self._event(self.threads[0], 'ok', latency_ms=250)
        code, output = self._run()
        self.assertEqual(code, 0)
        self.assertIn('terminal_total           = 1', output)
        self.assertIn('outcome_ok', output)
        self.assertIn('latency_p95_ms', output)
        self.assertIn('failure_rate', output)
        for thread in self.threads:
            self.assertNotIn(thread.pk, output)
        self.assertNotIn(self.user.username, output)

    def test_no_ok_rows_leaves_latency_na(self):
        # One filtered batch trips no threshold (a lone race_lost would push
        # race_rate to 1.0), so the verdict is PASS with no latency sample.
        self._event(self.threads[0], 'content_filter')
        code, output = self._run()
        self.assertEqual(code, 0)
        self.assertIn('latency_p95_ms           = n/a', output)
        with mock.patch(
            'aichat.management.commands.compaction_soak_report.timezone.now',
            return_value=timezone.now(),
        ):
            report = build_report(7)
        self.assertIsNone(report['latency_p95_ms'])
        self.assertTrue(report['thresholds']['latency_p95_ms']['pass'])
