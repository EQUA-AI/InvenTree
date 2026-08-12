"""S38: the thread-compaction job — lock, CAS watermark, protected merge."""

from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from aichat import tasks
from aichat.models import ChatMessage, ChatThread
from aichat.services.threads import ThreadRepository


def _summary_payload(label='Pump 3 diagnosis', **overrides):
    payload = {
        'label': label,
        'open_questions': ['is the seal OEM?'],
        'pending_proposals': [],
        'machine_facts': ['pump 3 seal worn'],
        'corrections': [],
        'citation_keys': ['manual:pump3:seals'],
        'narrative': 'Diagnosed a worn seal on pump 3.',
    }
    payload.update(overrides)
    return payload


class MergeHelpersTest(TestCase):
    """Pure helpers: summary parsing and the protected-field merge."""

    def test_parse_summary_body_reads_json_under_the_label_line(self):
        summary = 'Label\n{"machine_facts": ["fact"]}'
        self.assertEqual(tasks.parse_summary_body(summary), {'machine_facts': ['fact']})
        self.assertEqual(tasks.parse_summary_body('label only, no body'), {})
        self.assertEqual(tasks.parse_summary_body(''), {})

    def test_protected_fields_union_prior_first_with_cap(self):
        prior = {'machine_facts': ['old fact', 'shared']}
        fresh = _summary_payload(machine_facts=['shared', 'new fact'])
        merged = tasks.merge_protected_fields(prior, fresh)
        self.assertEqual(merged['machine_facts'], ['old fact', 'shared', 'new fact'])

    def test_protected_fields_cap_bounds_growth(self):
        prior = {'machine_facts': [f'fact {i}' for i in range(30)]}
        merged = tasks.merge_protected_fields(prior, _summary_payload(machine_facts=[]))
        self.assertEqual(len(merged['machine_facts']), tasks.COMPACTION_PROTECTED_CAP)


class CompactionJobTest(TestCase):
    """The job summarizes above the watermark and CAS-advances it."""

    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username='compact-user')
        self.thread = ChatThread.objects.create(
            owner=self.user, scope_key='k', scope_hash='h', namespace='unscoped'
        )
        for i in range(1, 21):
            ChatMessage.objects.create(
                thread=self.thread,
                sequence=i,
                role='user' if i % 2 else 'assistant',
                content=f'message {i}',
            )
        self.thread.next_sequence = 21
        self.thread.save(update_fields=['next_sequence'])

    def test_job_writes_label_line_and_advances_watermark(self):
        with mock.patch.object(
            tasks, '_summarize', return_value=_summary_payload()
        ) as summarize:
            tasks.compact_thread_summary(self.thread.pk)

        self.thread.refresh_from_db()
        self.assertEqual(self.thread.summary_through_sequence, 20)
        first_line, _, body = self.thread.summary.partition('\n')
        self.assertEqual(first_line, 'Pump 3 diagnosis')
        self.assertIn('pump 3 seal worn', body)
        # The transcript sent to the model covers exactly the backlog.
        transcript = summarize.call_args.args[0]
        self.assertEqual(len(transcript), 20)

    def test_prior_protected_facts_merge_forward(self):
        self.thread.summary = 'Old label\n{"machine_facts": ["ancient fact"]}'
        self.thread.summary_through_sequence = 2
        self.thread.save(update_fields=['summary', 'summary_through_sequence'])

        with mock.patch.object(tasks, '_summarize', return_value=_summary_payload()):
            tasks.compact_thread_summary(self.thread.pk)

        self.thread.refresh_from_db()
        self.assertIn('ancient fact', self.thread.summary)
        self.assertIn('pump 3 seal worn', self.thread.summary)

    def test_lock_contention_is_a_noop(self):
        cache.add(f'aimms:compaction:{self.thread.pk}', True, timeout=60)
        with mock.patch.object(tasks, '_summarize') as summarize:
            tasks.compact_thread_summary(self.thread.pk)
        summarize.assert_not_called()
        self.thread.refresh_from_db()
        self.assertEqual(self.thread.summary_through_sequence, 0)

    def test_lost_watermark_race_is_a_noop(self):
        def _move_watermark(*args, **kwargs):
            ChatThread.objects.filter(pk=self.thread.pk).update(
                summary_through_sequence=5
            )
            return _summary_payload()

        with mock.patch.object(tasks, '_summarize', side_effect=_move_watermark):
            tasks.compact_thread_summary(self.thread.pk)

        self.thread.refresh_from_db()
        self.assertEqual(self.thread.summary_through_sequence, 5)
        self.assertEqual(self.thread.summary, '')

    def test_small_backlog_never_summarizes(self):
        self.thread.summary_through_sequence = 10
        self.thread.save(update_fields=['summary_through_sequence'])
        with mock.patch.object(tasks, '_summarize') as summarize:
            tasks.compact_thread_summary(self.thread.pk)
        summarize.assert_not_called()

    def test_summarize_failure_leaves_state_untouched(self):
        with mock.patch.object(tasks, '_summarize', side_effect=RuntimeError('llm down')):
            tasks.compact_thread_summary(self.thread.pk)
        self.thread.refresh_from_db()
        self.assertEqual(self.thread.summary_through_sequence, 0)
        self.assertEqual(self.thread.summary, '')


class CompactionTriggerTest(TestCase):
    """The terminal-path trigger is flag-gated and backlog-gated."""

    def setUp(self):
        cache.clear()
        self.repo = ThreadRepository.__new__(ThreadRepository)

    def _thread(self, next_sequence=40, watermark=0):
        from types import SimpleNamespace

        return SimpleNamespace(
            pk='t1', next_sequence=next_sequence, summary_through_sequence=watermark
        )

    def _settings(self, shadow=True, full=False):
        from ai.core.config import Settings

        return Settings(
            _env_file=None,
            FEATURE_THREAD_COMPACTION_SHADOW=shadow,
            FEATURE_THREAD_COMPACTION=full,
        )

    def test_trigger_offloads_when_backlog_is_large(self):
        with (
            mock.patch('ai.core.config.get_settings', return_value=self._settings()),
            mock.patch('InvenTree.tasks.offload_task') as offload,
        ):
            self.repo._maybe_schedule_compaction(self._thread())
        offload.assert_called_once()
        self.assertTrue(offload.call_args.kwargs.get('force_async'))

    def test_trigger_skips_small_backlog(self):
        with (
            mock.patch('ai.core.config.get_settings', return_value=self._settings()),
            mock.patch('InvenTree.tasks.offload_task') as offload,
        ):
            self.repo._maybe_schedule_compaction(self._thread(next_sequence=10))
        offload.assert_not_called()

    def test_trigger_skips_when_flags_off(self):
        with (
            mock.patch(
                'ai.core.config.get_settings',
                return_value=self._settings(shadow=False, full=False),
            ),
            mock.patch('InvenTree.tasks.offload_task') as offload,
        ):
            self.repo._maybe_schedule_compaction(self._thread())
        offload.assert_not_called()
