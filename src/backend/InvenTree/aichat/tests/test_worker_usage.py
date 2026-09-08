"""M2 PR 2 (§8.4, GR-29): the worker usage ledger and the daily token cap."""

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from unittest import mock

from django.db import DatabaseError
from django.test import TestCase

from aichat import tasks
from aichat.models import (
    AIWorkerUsageEvent,
    AIWorkerUsagePurpose,
    ChatCompactionOutcome,
    ChatThread,
)
from aichat.services import worker_usage
from aichat.tests.test_compaction_events import (
    _ai_settings,
    _FakeAzureOpenAI,
    _FakeCompletions,
    _summary_payload,
    _ThreadMixin,
    content_filter_error,
)

DZ_DEPLOYMENT = 'gpt-5-mini-dz'
SUMMARIZATION = AIWorkerUsagePurpose.SUMMARIZATION
EXTRACTION = AIWorkerUsagePurpose.EXTRACTION


def _rows():
    return list(AIWorkerUsageEvent.objects.order_by('pk'))


class LedgerWriteTest(_ThreadMixin, TestCase):
    """One AIWorkerUsageEvent row per compaction run, whatever the outcome."""

    def test_row_per_compaction_stamps_dz_deployment_from_stats(self):
        # D-10 proof: the ledger's deployment is what the worker's override
        # resolved to, read from the same stats the compaction event uses.
        _FakeAzureOpenAI.completions = _FakeCompletions([_summary_payload()])
        with (
            mock.patch(
                'ai.core.config.get_settings',
                return_value=_ai_settings(
                    AZURE_OPENAI_SUMMARIZATION_DEPLOYMENT=DZ_DEPLOYMENT
                ),
            ),
            mock.patch('openai.AzureOpenAI', _FakeAzureOpenAI),
        ):
            tasks.compact_thread_summary(self.thread.pk)

        (row,) = _rows()
        self.assertEqual(row.purpose, 'summarization')
        self.assertEqual(row.task, 'compact_thread_summary')
        self.assertEqual(row.thread_id, self.thread.pk)
        self.assertEqual(row.deployment, DZ_DEPLOYMENT)
        self.assertEqual((row.input_tokens, row.output_tokens), (120, 30))
        self.assertEqual(row.attempts, 1)
        self.assertIsNotNone(row.created_at)
        (event,) = self._events()
        self.assertEqual(event.outcome, 'ok')
        self.assertEqual(event.deployment, DZ_DEPLOYMENT)
        self.assertEqual((event.input_tokens, event.output_tokens), (120, 30))
        self.assertNotIn('message 1', str(row))

    def test_attempts_fold_a_content_filter_bisect_into_one_row(self):
        _FakeAzureOpenAI.completions = _FakeCompletions([
            content_filter_error(),
            _summary_payload(),
        ])
        with (
            mock.patch('openai.AzureOpenAI', _FakeAzureOpenAI),
            self.assertLogs('inventree', level='WARNING'),
        ):
            tasks.compact_thread_summary(self.thread.pk)

        self.assertEqual(len(_FakeAzureOpenAI.completions.calls), 2)
        (row,) = _rows()
        self.assertEqual(row.attempts, 2)
        # The refused call raised before any usage came back.
        self.assertEqual((row.input_tokens, row.output_tokens), (120, 30))
        self.assertEqual(row.deployment, 'standard-4o')
        self.assertEqual(self._events()[0].outcome, 'content_filter')

    def test_three_refusals_still_land_one_row(self):
        _FakeAzureOpenAI.completions = _FakeCompletions([
            content_filter_error(),
            content_filter_error(),
            content_filter_error(),
        ])
        with (
            mock.patch('openai.AzureOpenAI', _FakeAzureOpenAI),
            self.assertLogs('inventree', level='WARNING'),
        ):
            tasks.compact_thread_summary(self.thread.pk)
        (row,) = _rows()
        self.assertEqual(row.attempts, 3)
        self.assertEqual((row.input_tokens, row.output_tokens), (0, 0))

    def test_failed_call_is_still_recorded_with_zero_tokens(self):
        with mock.patch.object(
            tasks, '_summarize', side_effect=RuntimeError('llm down: secret text')
        ):
            tasks.compact_thread_summary(self.thread.pk)
        (row,) = _rows()
        self.assertEqual(row.attempts, 1)
        self.assertEqual((row.input_tokens, row.output_tokens), (0, 0))
        self.assertEqual(row.deployment, '')
        self.assertEqual(row.thread_id, self.thread.pk)
        self.assertEqual(self._events()[0].outcome, 'failed')

    def test_flags_off_skip_writes_no_ledger_row(self):
        with (
            mock.patch(
                'ai.core.config.get_settings',
                return_value=_ai_settings(FEATURE_THREAD_COMPACTION_SHADOW=False),
            ),
            mock.patch.object(tasks, '_summarize') as summarize,
        ):
            tasks.compact_thread_summary(self.thread.pk)
        summarize.assert_not_called()
        self.assertEqual(_rows(), [])
        self.assertEqual(self._events()[0].outcome, 'skipped')

    def test_ledger_write_failure_never_breaks_compaction(self):
        with (
            mock.patch.object(
                AIWorkerUsageEvent.objects,
                'create',
                side_effect=DatabaseError('ledger down'),
            ),
            mock.patch.object(tasks, '_summarize', return_value=_summary_payload()),
            self.assertLogs('inventree', level='WARNING') as captured,
        ):
            tasks.compact_thread_summary(self.thread.pk)
        self.thread.refresh_from_db()
        self.assertEqual(self.thread.summary_through_sequence, 20)
        self.assertIn('pump 3 seal worn', self.thread.summary)
        self.assertEqual(self._events()[0].outcome, 'ok')
        joined = ' '.join(captured.output)
        self.assertIn('ledger write failed', joined)
        self.assertIn('DatabaseError', joined)
        self.assertNotIn('ledger down', joined)

    def test_thread_delete_keeps_the_spend_row(self):
        with mock.patch.object(tasks, '_summarize', return_value=_summary_payload()):
            tasks.compact_thread_summary(self.thread.pk)
        ChatThread.objects.filter(pk=self.thread.pk).delete()
        (row,) = _rows()
        self.assertIsNone(row.thread_id)


class DailyCapTest(_ThreadMixin, TestCase):
    """At the cap the run records budget_deferred and never calls the model."""

    def _settings(self, cap, **overrides):
        return mock.patch(
            'ai.core.config.get_settings',
            return_value=_ai_settings(
                AIMMS_WORKER_DAILY_TOKEN_CAP_SUMMARIZATION=cap, **overrides
            ),
        )

    def _spend(self, purpose=SUMMARIZATION, input_tokens=900, output_tokens=100):
        return worker_usage.record_worker_usage(
            purpose,
            'standard-4o',
            task='compact_thread_summary',
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )

    def test_cap_reached_defers_without_summarizing(self):
        self._spend()  # 1000 tokens today
        with (
            self._settings(1000),
            mock.patch.object(tasks, '_summarize') as summarize,
            self.assertLogs('inventree', level='INFO') as captured,
        ):
            tasks.compact_thread_summary(self.thread.pk)

        summarize.assert_not_called()
        (event,) = self._events()
        self.assertEqual(event.outcome, 'budget_deferred')
        self.assertEqual(event.error_code, 'daily_cap')
        self.assertEqual(event.flag_state, 'shadow')
        self.assertIsNotNone(event.finished_at)
        self.assertEqual((event.from_sequence, event.through_sequence), (1, 20))
        self.assertEqual(event.message_count, 20)
        self.assertEqual((event.input_tokens, event.output_tokens), (0, 0))
        self.thread.refresh_from_db()
        self.assertEqual(self.thread.summary_through_sequence, 0)
        self.assertEqual(self.thread.summary, '')
        # No model call, no spend row: the ledger still holds only the fixture.
        self.assertEqual(AIWorkerUsageEvent.objects.count(), 1)
        joined = ' '.join(captured.output)
        self.assertIn('budget deferred', joined)
        self.assertIn('used=1000 cap=1000', joined)

    def test_below_cap_proceeds(self):
        self._spend(input_tokens=900, output_tokens=99)
        with (
            self._settings(1000),
            mock.patch.object(tasks, '_summarize', return_value=_summary_payload()),
        ):
            tasks.compact_thread_summary(self.thread.pk)
        self.assertEqual(self._events()[0].outcome, 'ok')
        self.assertEqual(AIWorkerUsageEvent.objects.count(), 2)

    def test_cap_zero_is_unlimited(self):
        self._spend(input_tokens=5_000_000, output_tokens=5_000_000)
        with (
            self._settings(0),
            mock.patch.object(tasks, '_summarize', return_value=_summary_payload()),
            mock.patch.object(
                worker_usage, 'usage_today', wraps=worker_usage.usage_today
            ) as usage,
        ):
            tasks.compact_thread_summary(self.thread.pk)
        usage.assert_not_called()
        self.assertEqual(self._events()[0].outcome, 'ok')
        self.thread.refresh_from_db()
        self.assertEqual(self.thread.summary_through_sequence, 20)

    def test_other_purpose_spend_does_not_count(self):
        self._spend(purpose=EXTRACTION, input_tokens=5000, output_tokens=500)
        with (
            self._settings(1000),
            mock.patch.object(tasks, '_summarize', return_value=_summary_payload()),
        ):
            tasks.compact_thread_summary(self.thread.pk)
        self.assertEqual(self._events()[0].outcome, 'ok')

    def test_cap_read_db_error_lets_compaction_proceed(self):
        with (
            self._settings(1),
            mock.patch.object(
                worker_usage, 'usage_today', side_effect=DatabaseError('pg down')
            ),
            mock.patch.object(tasks, '_summarize', return_value=_summary_payload()),
            self.assertLogs('inventree', level='WARNING') as captured,
        ):
            tasks.compact_thread_summary(self.thread.pk)
        self.assertEqual(self._events()[0].outcome, 'ok')
        self.thread.refresh_from_db()
        self.assertEqual(self.thread.summary_through_sequence, 20)
        joined = ' '.join(captured.output)
        self.assertIn('cap read failed', joined)
        self.assertIn('DatabaseError', joined)
        self.assertNotIn('pg down', joined)

    def test_yesterdays_spend_does_not_count(self):
        row = self._spend(input_tokens=5000, output_tokens=500)
        AIWorkerUsageEvent.objects.filter(pk=row.pk).update(
            created_at=worker_usage.utc_day_bounds()[0] - timedelta(seconds=1)
        )
        with (
            self._settings(1000),
            mock.patch.object(tasks, '_summarize', return_value=_summary_payload()),
        ):
            tasks.compact_thread_summary(self.thread.pk)
        self.assertEqual(self._events()[0].outcome, 'ok')


class ServiceHelpersTest(TestCase):
    """record_worker_usage / usage_today / cap_for in isolation."""

    def _row(self, purpose, when, input_tokens=10, output_tokens=1):
        row = worker_usage.record_worker_usage(
            purpose,
            'standard-4o',
            input_tokens=input_tokens,
            output_tokens=output_tokens,
        )
        AIWorkerUsageEvent.objects.filter(pk=row.pk).update(created_at=when)
        return row

    def test_usage_today_is_bounded_by_utc_midnight(self):
        utc = dt_timezone.utc
        now = datetime(2026, 9, 8, 12, 0, tzinfo=utc)
        self._row(SUMMARIZATION, datetime(2026, 9, 8, 0, 0, 0, tzinfo=utc), 100, 10)
        self._row(SUMMARIZATION, datetime(2026, 9, 8, 23, 59, 59, tzinfo=utc), 5, 1)
        self._row(SUMMARIZATION, datetime(2026, 9, 7, 23, 59, 59, tzinfo=utc), 1000, 1)
        self._row(SUMMARIZATION, datetime(2026, 9, 9, 0, 0, 0, tzinfo=utc), 1000, 1)
        self._row(EXTRACTION, datetime(2026, 9, 8, 12, 0, 0, tzinfo=utc), 1000, 1)

        self.assertEqual(
            worker_usage.usage_today(SUMMARIZATION, now=now),
            {'input_tokens': 105, 'output_tokens': 11, 'rows': 2},
        )
        self.assertEqual(
            worker_usage.usage_today(EXTRACTION, now=now),
            {'input_tokens': 1000, 'output_tokens': 1, 'rows': 1},
        )
        # A non-UTC clock resolves to the UTC day: 01:00+03:00 is still the 7th.
        local = datetime(2026, 9, 8, 1, 0, tzinfo=dt_timezone(timedelta(hours=3)))
        self.assertEqual(
            worker_usage.usage_today(SUMMARIZATION, now=local),
            {'input_tokens': 1000, 'output_tokens': 1, 'rows': 1},
        )
        self.assertEqual(
            worker_usage.usage_today(
                SUMMARIZATION, now=datetime(2026, 1, 1, tzinfo=utc)
            ),
            {'input_tokens': 0, 'output_tokens': 0, 'rows': 0},
        )

    def test_utc_day_bounds(self):
        start, end = worker_usage.utc_day_bounds(
            datetime(2026, 9, 8, 22, 30, tzinfo=dt_timezone(timedelta(hours=-5)))
        )
        self.assertEqual(start, datetime(2026, 9, 9, tzinfo=dt_timezone.utc))
        self.assertEqual(end - start, timedelta(days=1))

    def test_zero_attempts_writes_nothing(self):
        self.assertIsNone(
            worker_usage.record_worker_usage(SUMMARIZATION, 'x', attempts=0)
        )
        self.assertIsNone(
            worker_usage.record_worker_usage(SUMMARIZATION, 'x', attempts=-3)
        )
        self.assertEqual(AIWorkerUsageEvent.objects.count(), 0)

    def test_record_never_raises(self):
        with (
            mock.patch.object(
                AIWorkerUsageEvent.objects,
                'create',
                side_effect=DatabaseError('ledger down'),
            ),
            self.assertLogs('inventree', level='WARNING') as captured,
        ):
            self.assertIsNone(
                worker_usage.record_worker_usage(SUMMARIZATION, 'x', attempts=1)
            )
        self.assertNotIn('ledger down', ' '.join(captured.output))

    def test_record_coerces_and_bounds_counters(self):
        row = worker_usage.record_worker_usage(
            SUMMARIZATION,
            'd' * 200,
            input_tokens='12',
            output_tokens=None,
            attempts=100_000,
        )
        self.assertEqual(len(row.deployment), 128)
        self.assertEqual((row.input_tokens, row.output_tokens), (12, 0))
        self.assertEqual(row.attempts, 32767)

    def test_cap_for_reads_the_purpose_field(self):
        settings = _ai_settings(
            AIMMS_WORKER_DAILY_TOKEN_CAP_SUMMARIZATION=1234,
            AIMMS_WORKER_DAILY_TOKEN_CAP_EXTRACTION=99,
        )
        self.assertEqual(worker_usage.cap_for(SUMMARIZATION, settings), 1234)
        self.assertEqual(worker_usage.cap_for(EXTRACTION, settings), 99)
        self.assertEqual(worker_usage.cap_for('topology', settings), 0)
        self.assertEqual(worker_usage.cap_for(SUMMARIZATION, _ai_settings()), 0)

    def test_daily_cap_status_shapes(self):
        settings = _ai_settings(AIMMS_WORKER_DAILY_TOKEN_CAP_SUMMARIZATION=50)
        self.assertEqual(
            worker_usage.daily_cap_status(SUMMARIZATION, settings),
            {'cap': 50, 'used': 0, 'reached': False},
        )
        worker_usage.record_worker_usage(
            SUMMARIZATION, 'x', input_tokens=40, output_tokens=10
        )
        self.assertEqual(
            worker_usage.daily_cap_status(SUMMARIZATION, settings),
            {'cap': 50, 'used': 50, 'reached': True},
        )
        self.assertEqual(
            worker_usage.daily_cap_status(EXTRACTION, settings),
            {'cap': 0, 'used': 0, 'reached': False},
        )

    def test_compaction_event_outcome_enum_has_budget_deferred(self):
        self.assertIn('budget_deferred', ChatCompactionOutcome.values)
