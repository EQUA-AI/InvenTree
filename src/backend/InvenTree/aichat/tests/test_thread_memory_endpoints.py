"""M2 PR 5: the inspection backend (plan of record §8.6 items 1, 2, 4; §9.11; GR-16).

The list and get routes ship the summary LABEL only, to owner and grantee
alike; the parsed body is owner-only on ``GET /threads/{id}/memory``; the
"This is wrong / forget" write is owner-only on
``PUT /threads/{id}/memory/corrections`` and never a prose edit.

Routes are driven the way ``ai/core/tests/test_evidence_set_endpoint.py``
does — the coroutine called directly with the boundary principal patched —
under ``async_to_sync`` so the thread-sensitive ORM hops land on this
test's connection (and transaction).
"""

import json
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from asgiref.sync import async_to_sync
from fastapi import HTTPException, Response
from pydantic import ValidationError

from ai.core import app as ai_app
from ai.core.auth import AIPrincipal
from ai.core.memory.summary_body import upgrade_body
from aichat.models import AIRetentionOutbox, ChatThread
from aichat.services import ThreadRepository
from aichat.services.summary_corrections import SummaryWriteRaceError

SCOPE = 'site:main'
LABEL = 'Motor bay pump'
FACT = 'the motor is 5.5 kW'
OLD_FACT = 'the motor is 4 kW'
QUESTION = 'which breaker feeds the pump?'


def _principal(user) -> AIPrincipal:
    return AIPrincipal(
        subject=f'user:{user.pk}',
        actor=f'user:{user.pk}',
        user_pk=str(user.pk),
        username=user.get_username(),
        authentication_method='session',
        scope=SCOPE,
        policy_version='test',
        is_staff=False,
        is_superuser=False,
    )


def _body() -> dict:
    body = upgrade_body(
        {
            'label': LABEL,
            'open_questions': [QUESTION],
            'pending_proposals': [],
            'machine_facts': [OLD_FACT, FACT],
            'corrections': [],
            'citation_keys': ['WO-12'],
            'narrative': 'The motor is 5.5 kW.',
        },
        created_seq=4,
    )
    # Ids count across the lists (oq1, mf2, mf3); mf2 was restated by mf3:
    # history the modal may show.
    body['machine_facts'][0]['lifecycle'] = 'superseded'
    body['machine_facts'][0]['superseded_by'] = 'mf3'
    body['machine_facts'][1]['directive_flags'] = ['nl_directive']
    return body


def _call(route, principal, *args, **kwargs):
    with mock.patch('ai.core.app._principal', return_value=principal):
        return async_to_sync(route)(*args, **kwargs)


@override_settings(FEATURE_THREAD_SHARING=True)
class _Base(TestCase):
    def setUp(self):
        users = get_user_model().objects
        self.owner = users.create_user(username='memory-owner')
        self.grantee = users.create_user(username='memory-grantee')
        self.owner_repo = ThreadRepository(self.owner.pk, SCOPE)
        self.thread, _ = self.owner_repo.get_or_create(title='Pump notes')
        ChatThread.objects.filter(pk=self.thread.pk).update(
            summary=LABEL + '\n' + json.dumps(_body()),
            summary_through_sequence=4,
            next_sequence=9,
        )
        self.owner_repo.share(self.thread.pk, grantee_id=self.grantee.pk)

    def _stored_body(self) -> dict:
        summary = ChatThread.objects.get(pk=self.thread.pk).summary
        return json.loads(summary.partition('\n')[2])


class LabelOnlyListingTest(_Base):
    """§8.6 item 1: ``/threads`` and ``/threads/{id}`` carry the label only."""

    def _assert_label_only(self, value: str):
        self.assertEqual(value, LABEL)
        self.assertNotIn('{', value)
        self.assertNotIn(FACT, value)
        self.assertNotIn(QUESTION, value)

    def test_owner_list_and_get_carry_the_label_only(self):
        """The owner's list row and detail carry line 1 only."""
        listing = _call(ai_app.list_threads, _principal(self.owner), limit=50, q=None)
        (row,) = [t for t in listing.threads if t.thread_id == self.thread.pk]
        self._assert_label_only(row.summary)
        self.assertEqual(listing.shared_threads, [])

        detail = _call(
            ai_app.get_thread,
            _principal(self.owner),
            self.thread.pk,
            include_messages=True,
            message_limit=50,
        )
        self._assert_label_only(detail['summary'])
        self.assertFalse(detail['shared'])
        self.assertEqual(detail['title'], 'Pump notes')

    def test_grantee_shared_row_and_get_carry_the_label_only(self):
        """A grantee's shared row and detail carry line 1 only."""
        listing = _call(ai_app.list_threads, _principal(self.grantee), limit=50, q=None)
        self.assertEqual(listing.threads, [])
        (row,) = listing.shared_threads
        self.assertEqual(row.thread_id, self.thread.pk)
        self.assertTrue(row.shared)
        self._assert_label_only(row.summary)

        detail = _call(
            ai_app.get_thread,
            _principal(self.grantee),
            self.thread.pk,
            include_messages=True,
            message_limit=50,
        )
        self.assertTrue(detail['shared'])
        self._assert_label_only(detail['summary'])

    def test_empty_summary_lists_as_an_empty_label(self):
        """No summary yet projects an empty label."""
        ChatThread.objects.filter(pk=self.thread.pk).update(summary='')
        listing = _call(ai_app.list_threads, _principal(self.owner), limit=50, q=None)
        (row,) = [t for t in listing.threads if t.thread_id == self.thread.pk]
        self.assertEqual(row.summary, '')


class GetThreadMemoryTest(_Base):
    """§8.6 item 2: the parsed body, owner only, history included."""

    def test_owner_sees_every_item_as_an_object(self):
        """Active and superseded items alike, as objects with ids."""
        response = Response()
        payload = _call(
            ai_app.get_thread_memory, _principal(self.owner), self.thread.pk, response
        )
        self.assertEqual(response.headers['Cache-Control'], 'private, no-store')
        self.assertEqual(payload.thread_id, self.thread.pk)
        self.assertEqual(payload.label, LABEL)
        self.assertEqual(payload.through_sequence, 4)
        self.assertEqual(payload.latest_sequence, 8)
        self.assertEqual(payload.body_version, 2)
        self.assertEqual(payload.citation_keys, ['WO-12'])
        self.assertEqual(payload.narrative, 'The motor is 5.5 kW.')
        self.assertEqual(payload.exclusions_count, 0)
        self.assertEqual(payload.pending_proposals, [])
        self.assertEqual(payload.corrections, [])

        (question,) = payload.open_questions
        self.assertEqual((question.id, question.text), ('oq1', QUESTION))
        self.assertEqual(question.lifecycle, 'active')
        self.assertEqual(question.memory_type, 'open_issue')
        self.assertEqual(question.created_seq, 4)

        old, new = payload.machine_facts
        self.assertEqual(
            (old.id, old.text, old.lifecycle), ('mf2', OLD_FACT, 'superseded')
        )
        self.assertEqual(old.superseded_by, 'mf3')
        self.assertEqual((new.id, new.text, new.lifecycle), ('mf3', FACT, 'active'))
        self.assertIsNone(new.superseded_by)
        self.assertEqual(new.directive_flags, ['nl_directive'])
        self.assertEqual(new.memory_type, 'equipment_fact')

    def test_legacy_string_body_reads_as_objects_without_a_write(self):
        """A pre-PR 3 body is upgraded in memory only."""
        legacy = {
            'label': LABEL,
            'open_questions': [],
            'pending_proposals': ['replace the seal'],
            'machine_facts': [FACT],
            'corrections': [],
            'citation_keys': [],
            'narrative': '',
        }
        stored = LABEL + '\n' + json.dumps(legacy)
        ChatThread.objects.filter(pk=self.thread.pk).update(summary=stored)
        payload = _call(
            ai_app.get_thread_memory, _principal(self.owner), self.thread.pk, Response()
        )
        self.assertEqual(
            [(i.id, i.text, i.lifecycle) for i in payload.pending_proposals],
            [('pp1', 'replace the seal', 'active')],
        )
        self.assertEqual([i.id for i in payload.machine_facts], ['mf2'])
        self.assertEqual(payload.body_version, 2)
        # Read-only: the stored summary is untouched.
        self.assertEqual(ChatThread.objects.get(pk=self.thread.pk).summary, stored)

    def test_empty_summary_projects_empty_lists(self):
        """No summary yet: empty lists, empty label, version 0."""
        ChatThread.objects.filter(pk=self.thread.pk).update(
            summary='', summary_through_sequence=0, next_sequence=1
        )
        payload = _call(
            ai_app.get_thread_memory, _principal(self.owner), self.thread.pk, Response()
        )
        self.assertEqual(payload.label, '')
        self.assertEqual((payload.through_sequence, payload.latest_sequence), (0, 0))
        self.assertEqual(payload.body_version, 0)
        for field in (
            'open_questions',
            'pending_proposals',
            'machine_facts',
            'corrections',
        ):
            self.assertEqual(getattr(payload, field), [])
        self.assertEqual(payload.citation_keys, [])
        self.assertEqual(payload.narrative, '')
        self.assertEqual(payload.exclusions_count, 0)

    def test_grantee_and_stranger_get_the_generic_404(self):
        """Non-owners get the same 404 as an unknown thread."""
        stranger = get_user_model().objects.create_user(username='memory-stranger')
        for user in (self.grantee, stranger):
            with self.assertRaises(HTTPException) as caught:
                _call(
                    ai_app.get_thread_memory,
                    _principal(user),
                    self.thread.pk,
                    Response(),
                )
            self.assertEqual(caught.exception.status_code, 404)
            self.assertEqual(caught.exception.detail, 'Thread not found')

    def test_unknown_thread_is_404_for_the_owner(self):
        """An unknown id is 404 for the owner too."""
        with self.assertRaises(HTTPException) as caught:
            _call(
                ai_app.get_thread_memory,
                _principal(self.owner),
                'no-such-thread',
                Response(),
            )
        self.assertEqual(caught.exception.status_code, 404)


class CorrectThreadMemoryTest(_Base):
    """§8.6 item 4: the owner's "This is wrong / forget" supersession."""

    def _put(self, user, item_id: str, action: str = 'wrong'):
        response = Response()
        request = ai_app.ThreadMemoryCorrectionRequest(item_id=item_id, action=action)
        payload = _call(
            ai_app.correct_thread_memory,
            _principal(user),
            self.thread.pk,
            request,
            response,
        )
        return payload, response

    def test_applied_marks_forgotten_and_enqueues_one_outbox_row(self):
        """The forget flips the item and owes one outbox row."""
        payload, response = self._put(self.owner, 'mf3', action='wrong')
        self.assertEqual(response.headers['Cache-Control'], 'private, no-store')
        self.assertEqual(
            (
                payload.thread_id,
                payload.item_id,
                payload.result,
                payload.through_sequence,
            ),
            (self.thread.pk, 'mf3', 'applied', 4),
        )
        body = self._stored_body()
        (item,) = [i for i in body['machine_facts'] if i['id'] == 'mf3']
        self.assertEqual(item['lifecycle'], 'forgotten')
        self.assertEqual(
            [(e['item_id'], e['reason']) for e in body['exclusions']],
            [('mf3', 'wrong')],
        )
        # Never a prose edit: the narrative is blanked, not rewritten.
        self.assertEqual(body['narrative'], '')
        (row,) = AIRetentionOutbox.objects.all()
        self.assertEqual((row.kind, row.reference), ('thread_summary', self.thread.pk))
        # The watermark never moves.
        thread = ChatThread.objects.get(pk=self.thread.pk)
        self.assertEqual(thread.summary_through_sequence, 4)
        self.assertTrue(thread.summary.startswith(LABEL + '\n'))

        memory = _call(
            ai_app.get_thread_memory, _principal(self.owner), self.thread.pk, Response()
        )
        self.assertEqual(memory.exclusions_count, 1)
        self.assertEqual(
            [(i.id, i.lifecycle) for i in memory.machine_facts],
            [('mf2', 'superseded'), ('mf3', 'forgotten')],
        )

    def test_repeating_the_correction_is_idempotent(self):
        """A repeat is applied without a second write or outbox row."""
        self._put(self.owner, 'oq1', action='forget')
        first = ChatThread.objects.get(pk=self.thread.pk).summary
        payload, _ = self._put(self.owner, 'oq1', action='forget')
        self.assertEqual(payload.result, 'applied')
        self.assertEqual(ChatThread.objects.get(pk=self.thread.pk).summary, first)
        self.assertEqual(AIRetentionOutbox.objects.count(), 1)

    def test_unknown_item_is_200_unknown_item_without_a_write(self):
        """An unknown item is a 200 result code, never a write."""
        before = ChatThread.objects.get(pk=self.thread.pk).summary
        payload, _ = self._put(self.owner, 'mf99')
        self.assertEqual(
            (payload.result, payload.through_sequence), ('unknown_item', 4)
        )
        self.assertEqual(ChatThread.objects.get(pk=self.thread.pk).summary, before)
        self.assertEqual(AIRetentionOutbox.objects.count(), 0)

    def test_grantee_and_stranger_get_the_generic_404_without_a_write(self):
        """Non-owners get the generic 404 and nothing changes."""
        stranger = get_user_model().objects.create_user(username='memory-stranger')
        before = ChatThread.objects.get(pk=self.thread.pk).summary
        for user in (self.grantee, stranger):
            with self.assertRaises(HTTPException) as caught:
                self._put(user, 'mf3')
            self.assertEqual(caught.exception.status_code, 404)
            self.assertEqual(caught.exception.detail, 'Thread not found')
        self.assertEqual(ChatThread.objects.get(pk=self.thread.pk).summary, before)
        self.assertEqual(AIRetentionOutbox.objects.count(), 0)

    def test_bad_item_id_or_action_is_rejected_by_the_request_model(self):
        """FastAPI turns a request-model ValidationError into a 422."""
        for item_id in ('', 'x' * 33, 'mf', 'zz1', 'mf1; drop', '[mf1]'):
            with self.assertRaises(ValidationError):
                ai_app.ThreadMemoryCorrectionRequest(item_id=item_id, action='wrong')
        with self.assertRaises(ValidationError):
            ai_app.ThreadMemoryCorrectionRequest(item_id='mf1', action='edit')
        accepted = ai_app.ThreadMemoryCorrectionRequest(item_id='co7', action='forget')
        self.assertEqual(accepted.model_dump(), {'item_id': 'co7', 'action': 'forget'})

    def test_request_carries_no_idempotency_key(self):
        """The item id is the idempotency key; no dead contract field.

        A client-minted key would advertise ``/chat``-style dedup/conflict
        semantics the route does not implement (a repeat is applied through
        ``forget_item``'s already-excluded branch), so neither the request
        model nor the generated wire contract carries one.
        """
        self.assertEqual(
            set(ai_app.ThreadMemoryCorrectionRequest.model_fields),
            {'item_id', 'action'},
        )
        # A legacy body sending one is neither rejected nor retained.
        legacy = ai_app.ThreadMemoryCorrectionRequest(
            item_id='mf3', action='forget', idempotency_key='k-1'
        )
        self.assertFalse(hasattr(legacy, 'idempotency_key'))
        self.assertEqual(legacy.model_dump(), {'item_id': 'mf3', 'action': 'forget'})
        # The CI-pinned wire contract agrees (byte-compared by --check).
        from aichat.management.commands.generate_wire_contract import Command

        rendered = Command()._render()
        start = rendered.index('export interface ThreadMemoryCorrectionRequest {')
        block = rendered[start : rendered.index('}', start)]
        self.assertNotIn('idempotency_key', block)
        self.assertIn('item_id: string;', block)
        self.assertIn("action: 'wrong' | 'forget';", block)

    def test_persistent_write_race_is_409(self):
        """The service's exhausted retries surface as 409."""
        with mock.patch(
            'aichat.services.summary_corrections.forget_item',
            side_effect=SummaryWriteRaceError(),
        ):
            with self.assertRaises(HTTPException) as caught:
                self._put(self.owner, 'mf3')
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(caught.exception.detail, 'Memory changed, retry')

    def test_other_service_failures_are_a_value_free_500(self):
        """Any other failure is a 500 that names only the class."""
        with (
            mock.patch(
                'aichat.services.summary_corrections.forget_item',
                side_effect=RuntimeError(FACT),
            ),
            self.assertLogs('ai.core.app', level='WARNING') as captured,
        ):
            with self.assertRaises(HTTPException) as caught:
                self._put(self.owner, 'mf3')
        self.assertEqual(caught.exception.status_code, 500)
        self.assertEqual(caught.exception.detail, 'Memory correction failed')
        joined = ' '.join(captured.output)
        self.assertIn('error=RuntimeError', joined)
        self.assertIn('item=mf3', joined)
        self.assertNotIn(FACT, joined)
