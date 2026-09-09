"""M2 PR 4 (plan §5.9 / §8.5.1, GR-06, GR-43): the worker-side redaction family.

The golden ``redaction-*`` cases score the assistant's answer; this file
proves the compaction path itself. A thread seeded with the four value
classes (credential, MFA code, phone number, an injury statement carrying
the injured worker's contact details) across text AND voice turns (§8.5.1)
is compacted through the fake client: the captured summarizer payload
carries ``[REDACTED:...]`` markers and no seed fragment, the stored summary
carries none, the worker log carries counts only, no span attribute carries
one, and the event row records the categories and the entropy shadow — zero
for the family-owned seeds, one for a family-free blob (§5.9).

The injury statement itself is special-category content (health); its
rejection as a memory proposal is the M3a classification gate (§5.9 point
2). At M2 the contract for that class is: the identifying details inside it
are redacted, and no log or span ever carries the statement.
"""

import json
from contextlib import contextmanager
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from ai.core.config import Settings
from aichat import tasks
from aichat.models import ChatCompactionEvent, ChatMessage, ChatThread, TurnModality

from .test_thread_compaction import _FakeAzureOpenAI, _FakeCompletions

#: Seed class -> the user turn that carries it (all synthetic).
SEEDS = {
    'credential': (
        'the HMI password is Tr0ub4dor-Pump3 and the vendor '
        'API token: aimms-synthetic-7Qx9Lm2Vp4Rz8Kt3Wn6Yb'  # gitleaks:allow (synthetic redaction seed)
    ),
    'mfa_code': 'my verification code is 493027, log the seal swap under it',
    'phone_number': 'the seal vendor on-call number is +1 (555) 013-4477',
    'injury_statement': (
        'Marek cut his hand on the coupling guard this morning and went to '
        'the clinic; his callback number is 555-014-2299 and his email is '
        'marek.k@example.com'
    ),
}

#: A family-free high-entropy blob (the island's ``ENTROPY_SEEDS['base64_blob']``
#: in ``ai/core/tests/test_redaction_entropy.py``, which cannot be imported
#: under the Django settings module): no category names it, so it is what
#: the §5.9 shadow counts.
ENTROPY_BLOB = 'aXk3Lm9+Qw2Vb7Rt5Yz1Nc8Pd4Fg6Hj0Ks2Ml5Nx=='

#: Seed class -> the sequence it lands on; the rest of the thread is filler.
SEED_SEQUENCE = dict(zip(SEEDS, (3, 7, 11, 15), strict=True))

#: Seed class -> turn modality (§8.5.1: text AND voice turns). The injury
#: statement arrives as a voice turn; the compaction transcript is
#: modality-blind, so it takes the same redaction path as the text turns.
SEED_MODALITY = {'injury_statement': TurnModality.VOICE}

#: The exact substrings that must never leave the worker unredacted.
FRAGMENTS = (
    'Tr0ub4dor-Pump3',
    '7Qx9Lm2Vp4Rz8Kt3Wn6Yb',  # gitleaks:allow (synthetic redaction seed)
    '493027',
    '013-4477',
    '014-2299',
    'marek.k@example.com',
)

#: The categories the seeds must land in on the event row.
EXPECTED_CATEGORIES = {'password', 'token', 'otp', 'phone', 'email'}


def _ai_settings() -> Settings:
    return Settings(
        _env_file=None,
        AZURE_OPENAI_ENDPOINT='https://example.openai.azure.com',
        AZURE_OPENAI_API_KEY='test-key',
        AZURE_OPENAI_DEPLOYMENT='standard-4o',
        AZURE_OPENAI_FAST_DEPLOYMENT='fast-mini',
        AZURE_OPENAI_SUMMARIZATION_DEPLOYMENT='',
        FEATURE_THREAD_COMPACTION_SHADOW=True,
    )


def _canned_summary() -> dict:
    return {
        'label': 'Pump 3 seal swap',
        'open_questions': ['is the seal OEM?'],
        'pending_proposals': [],
        'machine_facts': ['pump 3 seal worn', 'vendor gasket ships tonight'],
        'corrections': [],
        'citation_keys': ['manual:pump3:seals'],
        'narrative': 'the crew swapped the pump 3 seal',
    }


class RedactionFamilyTest(TestCase):
    """Seeded transcript in, markers out — payload, summary, log, span, event."""

    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username='redaction-family')
        self.thread = ChatThread.objects.create(
            owner=self.user, scope_key='k', scope_hash='h', namespace='unscoped'
        )
        seeded = {sequence: name for name, sequence in SEED_SEQUENCE.items()}
        for i in range(1, 21):
            name = seeded.get(i)
            ChatMessage.objects.create(
                thread=self.thread,
                sequence=i,
                role='user' if i % 2 else 'assistant',
                content=SEEDS[name] if name else f'message {i}',
                modality=SEED_MODALITY.get(name, TurnModality.TEXT),
            )
        self.thread.next_sequence = 21
        self.thread.save(update_fields=['next_sequence'])
        patcher = mock.patch('ai.core.config.get_settings', return_value=_ai_settings())
        patcher.start()
        self.addCleanup(patcher.stop)

    def _compact(self):
        """Run the job through the fake client; return (calls, span sets, logs)."""
        completions = _FakeCompletions(_canned_summary())
        _FakeAzureOpenAI.completions = completions
        span = mock.Mock()

        @contextmanager
        def _fake_span(name, **attrs):
            from ai.core.tracing import span_attrs

            span.opened = {'name': name, **span_attrs(**attrs)}
            yield span

        with (
            mock.patch('openai.AzureOpenAI', _FakeAzureOpenAI),
            mock.patch('ai.core.tracing.turn_span', _fake_span),
            self.assertLogs('inventree', level='INFO') as captured,
        ):
            tasks.compact_thread_summary(self.thread.pk)
        return completions.calls, span, captured.output

    def test_summarizer_payload_carries_markers_and_no_seed(self):
        calls, _, _ = self._compact()
        self.assertEqual(len(calls), 1)
        payload = calls[0]['messages'][1]['content']
        self.assertIn('[REDACTED:', payload)
        for category in EXPECTED_CATEGORIES:
            self.assertIn(f'[REDACTED:{category}]', payload)
        for fragment in FRAGMENTS:
            self.assertNotIn(fragment, payload)
        # The system prompt is fixed text; it never embeds the transcript.
        self.assertEqual(
            calls[0]['messages'][0]['content'], tasks._COMPACTION_SYSTEM_PROMPT
        )

    def test_voice_turn_takes_the_same_redaction_path(self):
        """The injury statement is a voice row; its details redact like text."""
        voice = ChatMessage.objects.filter(
            thread=self.thread, modality=TurnModality.VOICE
        )
        self.assertEqual(list(voice.values_list('sequence', flat=True)), [15])
        self.assertIn('014-2299', voice.get().content)
        calls, _, _ = self._compact()
        transcript = json.loads(calls[0]['messages'][1]['content'])['new_messages']
        turn = transcript[SEED_SEQUENCE['injury_statement'] - 1]['content']
        self.assertIn('[REDACTED:phone]', turn)
        self.assertIn('[REDACTED:email]', turn)
        self.assertIn('Marek cut his hand', turn)
        self.assertNotIn('014-2299', turn)
        self.assertNotIn('marek.k@example.com', turn)

    def test_stored_summary_carries_no_seed(self):
        self._compact()
        self.thread.refresh_from_db()
        self.assertEqual(self.thread.summary_through_sequence, 20)
        self.assertTrue(self.thread.summary.startswith('Pump 3 seal swap\n'))
        for fragment in FRAGMENTS:
            self.assertNotIn(fragment, self.thread.summary)

    def test_worker_log_carries_counts_only(self):
        _, _, output = self._compact()
        joined = ' '.join(output)
        self.assertIn('redaction counts=', joined)
        for category in EXPECTED_CATEGORIES:
            self.assertIn(f'{category}=', joined)
        for fragment in FRAGMENTS:
            self.assertNotIn(fragment, joined)
        for seed in SEEDS.values():
            self.assertNotIn(seed, joined)

    def test_span_attributes_carry_no_seed(self):
        _, span, _ = self._compact()
        self.assertEqual(span.opened['name'], 'aimms.compaction')
        set_calls = {
            call.args[0]: call.args[1] for call in span.set_attribute.call_args_list
        }
        self.assertEqual(set_calls['aimms.compaction_outcome'], 'ok')
        self.assertIsInstance(set_calls['aimms.compaction_entropy_flags'], int)
        self.assertEqual(set_calls['aimms.compaction_directives_dropped'], 0)
        self.assertEqual(set_calls['aimms.compaction_directives_flagged'], 0)
        rendered = repr(span.opened) + repr(set_calls)
        for fragment in FRAGMENTS:
            self.assertNotIn(fragment, rendered)

    def test_event_row_records_categories_and_an_integer_entropy_shadow(self):
        self._compact()
        (event,) = ChatCompactionEvent.objects.filter(thread=self.thread)
        self.assertEqual(event.outcome, 'ok')
        self.assertGreaterEqual(set(event.redacted_counts), EXPECTED_CATEGORIES)
        self.assertTrue(all(count >= 1 for count in event.redacted_counts.values()))
        # Every seed is family-owned, so the shadow has nothing left to count.
        self.assertEqual(event.entropy_flags, 0)
        self.assertEqual(event.directives_stripped, 0)
        self.assertEqual(event.directives_flagged, 0)
        for fragment in FRAGMENTS:
            self.assertNotIn(fragment, repr(event.redacted_counts))

    def test_family_free_blob_lands_on_the_event_row_and_the_span(self):
        """§5.9 shadow: a family-free high-entropy value is counted once.

        The count — never the value — reaches the log, the row and the span.
        """
        blob = ENTROPY_BLOB
        ChatMessage.objects.filter(thread=self.thread, sequence=5).update(
            content=f'the vendor portal id was {blob} last night'
        )
        calls, span, output = self._compact()
        # Not a family hit: the blob reaches the model (that is what the
        # shadow measures); it must reach nothing else.
        self.assertIn(blob, calls[0]['messages'][1]['content'])
        (event,) = ChatCompactionEvent.objects.filter(thread=self.thread)
        self.assertEqual(event.outcome, 'ok')
        self.assertEqual(event.entropy_flags, 1)
        set_calls = {
            call.args[0]: call.args[1] for call in span.set_attribute.call_args_list
        }
        self.assertEqual(set_calls['aimms.compaction_entropy_flags'], 1)
        joined = ' '.join(output)
        self.assertIn('entropy flags=1', joined)
        self.assertNotIn(blob, joined)
        self.assertNotIn(blob, repr(span.opened) + repr(set_calls))
        self.thread.refresh_from_db()
        self.assertNotIn(blob, self.thread.summary)
