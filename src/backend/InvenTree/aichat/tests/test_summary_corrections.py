"""M2 PR 3: the owner's forget on a summary item and its fan-out."""

import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from ai.core.config import Settings
from ai.core.memory.summary_body import (
    active_items,
    fingerprint,
    render_for_context,
    upgrade_body,
)
from aichat import tasks
from aichat.models import (
    AIRetentionOutbox,
    ChatCompactionEvent,
    ChatMessage,
    ChatThread,
)
from aichat.services.summary_corrections import (
    ForgetResult,
    SummaryWriteRaceError,
    excluded_residual,
    forget_item,
    forgotten_texts,
    reapply_exclusions,
    revives_forgotten_text,
    scrub_revived_prose,
)

FACT = 'the motor is 5.5 kW'


def _body(**overrides):
    body = {
        'label': 'Motor',
        'open_questions': ['which breaker?'],
        'pending_proposals': [],
        'machine_facts': [FACT, 'belt tension nominal'],
        'corrections': [],
        'citation_keys': [],
        'narrative': 'The motor is 5.5 kW and the belt is fine.',
    }
    body.update(overrides)
    return upgrade_body(body, created_seq=4)


class _Base(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username='forget-owner')
        self.other = get_user_model().objects.create_user(username='forget-other')
        self.thread = ChatThread.objects.create(
            owner=self.user,
            scope_key='k',
            scope_hash='h',
            namespace='unscoped',
            summary='Motor\n' + json.dumps(_body()),
            summary_through_sequence=4,
        )

    def _body(self):
        self.thread.refresh_from_db()
        return tasks.parse_summary_body(self.thread.summary)


class ForgetItemTest(_Base):
    """``forget_item`` flips, excludes, blanks and enqueues; idempotent."""

    def test_forget_marks_forgotten_appends_exclusion_blanks_narrative_and_enqueues(
        self,
    ):
        with self.assertLogs('inventree', level='INFO') as captured:
            result = forget_item(
                self.thread.pk, 'mf2', actor_pk=self.user.pk, reason='wrong'
            )
        self.assertEqual(result, ForgetResult.APPLIED)
        body = self._body()
        self.assertEqual(self.thread.summary_through_sequence, 4)
        self.assertTrue(self.thread.summary.startswith('Motor\n'))
        (item,) = [i for i in body['machine_facts'] if i['id'] == 'mf2']
        self.assertEqual(item['lifecycle'], 'forgotten')
        self.assertEqual(item['text'], FACT)
        self.assertEqual(
            body['exclusions'],
            [
                {
                    'fingerprint': fingerprint(FACT),
                    'item_id': 'mf2',
                    'created_seq': 4,
                    'reason': 'wrong',
                }
            ],
        )
        self.assertEqual(body['narrative'], '')
        self.assertEqual(
            [i['text'] for i in active_items(body, 'machine_facts')],
            ['belt tension nominal'],
        )
        row = AIRetentionOutbox.objects.get()
        self.assertEqual(
            (row.kind, row.reference, row.state),
            ('thread_summary', str(self.thread.pk), 'pending'),
        )
        joined = ' '.join(captured.output)
        self.assertIn('item=mf2', joined)
        self.assertIn('reason=wrong', joined)
        self.assertNotIn('motor', joined.lower())

    def test_forget_is_idempotent(self):
        forget_item(self.thread.pk, 'mf2', actor_pk=self.user.pk, reason='forget')
        self.thread.refresh_from_db()
        first = self.thread.summary
        result = forget_item(
            self.thread.pk, 'mf2', actor_pk=self.user.pk, reason='forget'
        )
        self.assertEqual(result, ForgetResult.APPLIED)
        self.thread.refresh_from_db()
        self.assertEqual(self.thread.summary, first)
        self.assertEqual(AIRetentionOutbox.objects.count(), 1)

    def test_unknown_item_and_missing_thread_results(self):
        self.assertEqual(
            forget_item(self.thread.pk, 'mf77', actor_pk=self.user.pk, reason='forget'),
            ForgetResult.UNKNOWN_ITEM,
        )
        self.assertEqual(
            forget_item(
                'no-such-thread', 'mf2', actor_pk=self.user.pk, reason='forget'
            ),
            ForgetResult.THREAD_NOT_FOUND,
        )
        with self.assertRaises(ValueError):
            forget_item(self.thread.pk, 'mf2', actor_pk=self.user.pk, reason='nuke')
        self.assertEqual(AIRetentionOutbox.objects.count(), 0)
        self.assertEqual(self._body()['exclusions'], [])

    def test_non_owner_is_thread_not_found(self):
        result = forget_item(
            self.thread.pk, 'mf2', actor_pk=self.other.pk, reason='forget'
        )
        self.assertEqual(result, ForgetResult.THREAD_NOT_FOUND)
        self.assertEqual(self._body()['exclusions'], [])

    def test_persistent_race_raises(self):
        with (
            mock.patch(
                'aichat.services.summary_corrections._cas_write', return_value=False
            ),
            self.assertRaises(SummaryWriteRaceError),
        ):
            forget_item(self.thread.pk, 'mf2', actor_pk=self.user.pk, reason='forget')
        self.assertEqual(AIRetentionOutbox.objects.count(), 0)

    def test_legacy_string_body_is_upgraded_on_forget(self):
        self.thread.summary = 'Legacy\n' + json.dumps(
            {'machine_facts': [FACT, 'other']}
        )
        self.thread.save(update_fields=['summary'])
        result = forget_item(
            self.thread.pk, 'mf1', actor_pk=self.user.pk, reason='forget'
        )
        self.assertEqual(result, ForgetResult.APPLIED)
        body = self._body()
        self.assertEqual(body['body_version'], 2)
        self.assertEqual(body['machine_facts'][0]['lifecycle'], 'forgotten')
        self.assertEqual(body['machine_facts'][1]['lifecycle'], 'active')


class ForgetSurvivesCompactionTest(_Base):
    """The golden: forget, re-run compaction restating the text, still absent."""

    def setUp(self):
        super().setUp()
        for i in range(5, 31):
            ChatMessage.objects.create(
                thread=self.thread,
                sequence=i,
                role='user' if i % 2 else 'assistant',
                content=f'message {i}',
            )
        self.thread.next_sequence = 31
        self.thread.save(update_fields=['next_sequence'])
        patcher = mock.patch(
            'ai.core.config.get_settings',
            return_value=Settings(
                _env_file=None,
                AZURE_OPENAI_ENDPOINT='https://example.openai.azure.com',
                AZURE_OPENAI_API_KEY='test-key',
                AZURE_OPENAI_DEPLOYMENT='standard-4o',
                FEATURE_THREAD_COMPACTION_SHADOW=True,
            ),
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_forgotten_item_never_returns_via_compaction(self):
        forget_item(self.thread.pk, 'mf2', actor_pk=self.user.pk, reason='forget')
        fresh = {
            'label': 'Motor',
            'open_questions': [],
            'pending_proposals': [],
            'machine_facts': ['The motor is 5.5 kW.', 'new fact'],
            'corrections': [],
            'citation_keys': [],
            'narrative': 'Restated: the motor is 5.5 kW.',
        }
        with mock.patch.object(tasks, '_summarize', return_value=fresh):
            tasks.compact_thread_summary(self.thread.pk)
        (event,) = ChatCompactionEvent.objects.filter(thread=self.thread)
        self.assertEqual(event.outcome, 'ok')
        # One hit for the restated list item, one for the restating narrative.
        self.assertEqual(event.tombstone_hits, 2)
        body = self._body()
        self.assertEqual(self.thread.summary_through_sequence, 30)
        self.assertEqual(
            [i['text'] for i in active_items(body, 'machine_facts')],
            ['belt tension nominal', 'new fact'],
        )
        self.assertEqual(body['narrative'], '')
        self.assertEqual(len(body['exclusions']), 1)
        rendered = render_for_context(self.thread.summary)
        self.assertNotIn('5.5 kW', rendered)
        self.assertIn('- new fact', rendered)


    def test_forgotten_text_restated_only_in_narrative_is_blanked(self):
        """A narrative-only restatement is rejected (§5.6 gate, GR-03)."""
        forget_item(self.thread.pk, 'mf2', actor_pk=self.user.pk, reason='forget')
        fresh = {
            'label': 'Motor',
            'open_questions': [],
            'pending_proposals': [],
            'machine_facts': ['new fact'],
            'corrections': [],
            'citation_keys': [],
            'narrative': 'Restated: the motor is 5.5 kW.',
        }
        with mock.patch.object(tasks, '_summarize', return_value=fresh):
            tasks.compact_thread_summary(self.thread.pk)
        (event,) = ChatCompactionEvent.objects.filter(thread=self.thread)
        self.assertEqual(event.outcome, 'ok')
        self.assertEqual(event.tombstone_hits, 1)
        body = self._body()
        self.assertEqual(body['narrative'], '')
        self.assertEqual(
            [i['text'] for i in active_items(body, 'machine_facts')],
            ['belt tension nominal', 'new fact'],
        )
        self.assertNotIn('5.5 kW', render_for_context(self.thread.summary))


class ReapplyAndResidualTest(_Base):
    """The outbox handler and probe bodies."""

    def test_reapply_exclusions_and_residual(self):
        self.assertEqual(excluded_residual(self.thread.pk), 0)
        self.assertEqual(reapply_exclusions(self.thread.pk), 0)

        # Hand-edit: the exclusion exists but the item is active again.
        body = _body(
            exclusions=[
                {
                    'fingerprint': fingerprint(FACT),
                    'item_id': 'mf2',
                    'created_seq': 4,
                    'reason': 'forget',
                }
            ]
        )
        self.thread.summary = 'Motor\n' + json.dumps(body)
        self.thread.save(update_fields=['summary'])
        self.assertEqual(excluded_residual(self.thread.pk), 1)
        self.assertEqual(reapply_exclusions(self.thread.pk), 1)
        self.assertEqual(excluded_residual(self.thread.pk), 0)
        self.assertEqual(reapply_exclusions(self.thread.pk), 0)
        stored = self._body()
        self.assertEqual(stored['machine_facts'][0]['lifecycle'], 'forgotten')
        self.assertEqual(stored['narrative'], '')
        self.assertEqual(self.thread.summary_through_sequence, 4)

    def test_reapply_and_residual_cover_the_prose(self):
        # Hand-edit: the item is forgotten but the narrative restates it.
        body = _body(
            narrative='So the motor is 5.5 kW after all.',
            exclusions=[
                {
                    'fingerprint': fingerprint(FACT),
                    'item_id': 'mf2',
                    'created_seq': 4,
                    'reason': 'forget',
                }
            ],
        )
        body['machine_facts'][0]['lifecycle'] = 'forgotten'
        self.thread.summary = 'Motor\n' + json.dumps(body)
        self.thread.save(update_fields=['summary'])
        self.assertEqual(excluded_residual(self.thread.pk), 1)
        self.assertEqual(reapply_exclusions(self.thread.pk), 1)
        self.assertEqual(excluded_residual(self.thread.pk), 0)
        self.assertEqual(self._body()['narrative'], '')
        self.assertTrue(self.thread.summary.startswith('Motor\n'))

    def test_missing_thread_is_clean(self):
        self.assertEqual(reapply_exclusions('no-such-thread'), 0)
        self.assertEqual(excluded_residual('no-such-thread'), 0)


class ProseRevivalHelpersTest(TestCase):
    """The pure GR-03 prose helpers: token-bounded, normalization-aware."""

    def test_forgotten_texts_and_revival_are_token_bounded(self):
        body = _body(machine_facts=[FACT, 'belt ok', 'kept fact'])
        body['machine_facts'][0]['lifecycle'] = 'forgotten'
        body['machine_facts'][1]['lifecycle'] = 'forgotten'
        self.assertEqual(forgotten_texts(body), ['the motor is 5 5 kw', 'belt ok'])
        self.assertTrue(revives_forgotten_text(body, 'Restated: THE MOTOR IS 5.5 kW!'))
        self.assertTrue(revives_forgotten_text(body, 'belt ok now'))
        self.assertFalse(revives_forgotten_text(body, 'the belt okay'))
        self.assertFalse(revives_forgotten_text(body, 'kept fact'))
        self.assertFalse(revives_forgotten_text(body, ''))

    def test_scrub_revived_prose_blanks_narrative_and_label(self):
        body = _body(narrative='The motor is 5.5 kW.', label='The motor is 5.5 kW')
        body['machine_facts'][0]['lifecycle'] = 'forgotten'
        body, hits = scrub_revived_prose(body)
        self.assertEqual(hits, 2)
        self.assertEqual((body['narrative'], body['label']), ('', ''))
        self.assertEqual(scrub_revived_prose(body)[1], 0)
        clean = _body()
        self.assertEqual(scrub_revived_prose(clean)[1], 0)
        self.assertEqual(clean['narrative'], 'The motor is 5.5 kW and the belt is fine.')
