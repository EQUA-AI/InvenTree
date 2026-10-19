"""Native Work-focus receipts retain owner, role and customer/client boundaries."""

from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core import signing
from django.test import TestCase, override_settings

from tasks.models import WorkOrder

from aichat.models import ChatTurn, TurnModality, TurnState
from aichat.services.threads import (
    IdempotencyConflict,
    InvalidBoundary,
    ThreadNotFound,
    ThreadRepository,
)
from aichat.services.work_focus import (
    WORK_FOCUS_SNAPSHOT_KEY,
    WorkFocusError,
    public_focus,
)
from assets.models import AssetMachine, Client, ClientScopeGrant
from company.models import Company
from users.models import RuleSet


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver'
)
class WorkFocusTests(TestCase):
    """Use stored normal-reader roles, native grants and real durable turns."""

    def setUp(self):
        """Create authorized, foreign, customer-attributed and unresolved work."""
        self.actor = get_user_model().objects.create_user(username='work-focus-reader')
        self.other = get_user_model().objects.create_user(username='work-focus-other')
        self.group = Group.objects.create(name='Work focus readers')
        self.actor.groups.add(self.group)
        self.other.groups.add(self.group)
        self.role, _ = RuleSet.objects.update_or_create(
            group=self.group, name='work_order', defaults={'can_view': True}
        )
        self.client = Client.objects.create(name='Focus client', code='focus-client')
        self.grant = ClientScopeGrant.objects.create(
            user=self.actor, client=self.client
        )
        ClientScopeGrant.objects.create(user=self.other, client=self.client)
        self.machine = AssetMachine.objects.create(
            name='Focus pump', client=self.client
        )
        self.work = WorkOrder.objects.create(title='Focus work', machine=self.machine)
        self.second_work = WorkOrder.objects.create(
            title='Second work', machine=self.machine
        )
        foreign = Client.objects.create(name='Foreign focus', code='focus-foreign')
        foreign_machine = AssetMachine.objects.create(
            name='Foreign pump', client=foreign
        )
        self.foreign_work = WorkOrder.objects.create(
            title='Foreign', machine=foreign_machine
        )
        self.customer_work = WorkOrder.objects.create(
            title='Customer owns this work',
            machine=self.machine,
            customer=Company.objects.create(name='Focus customer', is_customer=True),
        )
        self.unresolved = WorkOrder.objects.create(title='No boundary')
        self.repository = ThreadRepository(self.actor, 'site:focus')
        self.thread, _ = self.repository.get_or_create()

    def acknowledge(self, work=None, version=None):
        """Request the exact current lifecycle version of a native record."""
        work = work or self.work
        return self.repository.acknowledge_work_focus(
            self.thread.pk,
            work.pk,
            expected_version=work.lifecycle_version if version is None else version,
        )

    def begin(self, receipt=None, key='focus-turn', fingerprint='a' * 64, **kwargs):
        """Exercise the existing durable repository, not a fake turn store."""
        return self.repository.begin_turn(
            self.thread.pk,
            content='Inspect this exact Work record.',
            modality=TurnModality.TEXT,
            trusted_context=kwargs.get(
                'trusted_context', {'actor': str(self.actor.pk)}
            ),
            modality_metadata={},
            idempotency_key=key,
            request_fingerprint=fingerprint,
            correlation_id='focus-correlation',
            work_focus_receipt=receipt,
        )

    def test_acknowledgement_is_exact_and_does_not_change_analysis_scope(self):
        """Work and machine/card identity remain distinct from scope narrowing."""
        before = self.repository.get_scope(self.thread.pk)
        result = self.acknowledge()
        self.assertEqual(result['schema_version'], 1)
        self.assertEqual(result['thread_id'], self.thread.pk)
        self.assertEqual(result['expires_in'], 300)
        self.assertEqual(result['focus']['work_order_id'], self.work.pk)
        self.assertEqual(result['focus']['machine_id'], self.machine.pk)
        self.assertEqual(
            result['focus']['lifecycle_version'], self.work.lifecycle_version
        )
        self.assertEqual(result['focus']['kind'], 'work_order')
        self.assertEqual(len(result['focus']['boundary_hash']), 64)
        self.assertEqual(self.repository.get_scope(self.thread.pk), before)
        self.assertFalse(ChatTurn.objects.exists())

    def test_unknown_foreign_customer_and_unresolved_work_reject_identically(self):
        """Client access never confers a customer-attributed Work boundary."""
        for work in [self.foreign_work, self.customer_work, self.unresolved]:
            with (
                self.subTest(work=work.pk),
                self.assertRaisesMessage(WorkFocusError, 'work_focus_rejected'),
            ):
                self.acknowledge(work)
        with self.assertRaisesMessage(WorkFocusError, 'work_focus_rejected'):
            self.repository.acknowledge_work_focus(
                self.thread.pk, 9999999, expected_version=1
            )

    def test_candidate_ids_and_versions_are_strict_positive_integers(self):
        """Booleans and numeric strings cannot become canonical identity."""
        for value in [True, False, '1', 0, -1, None, 1.5]:
            with self.subTest(value=value), self.assertRaises(WorkFocusError):
                self.repository.acknowledge_work_focus(
                    self.thread.pk, value, expected_version=1
                )
            with self.subTest(version=value), self.assertRaises(WorkFocusError):
                self.acknowledge(version=value if value is not None else 'invalid')

    def test_acknowledgement_requires_current_lifecycle_version(self):
        """A stale version is refused rather than relabeled as current."""
        with self.assertRaises(WorkFocusError):
            self.acknowledge(version=self.work.lifecycle_version + 1)

    def test_native_view_role_is_required_and_not_cached(self):
        """An already-created repository cannot retain a revoked role."""
        self.role.can_view = False
        self.role.save()
        with self.assertRaises(WorkFocusError):
            self.acknowledge()

    def test_acknowledgement_is_owner_only(self):
        """A same-client reader cannot mint focus on another user's thread."""
        other_repository = ThreadRepository(self.other, 'site:focus')
        with self.assertRaises(ThreadNotFound):
            other_repository.acknowledge_work_focus(
                self.thread.pk, self.work.pk, expected_version=1
            )

    def test_focus_snapshot_is_bound_to_the_durable_turn(self):
        """Later acknowledgements never retarget an earlier saved request."""
        acknowledgement = self.acknowledge()
        result = self.begin(acknowledgement['receipt'])
        self.acknowledge(self.second_work)
        result.turn.refresh_from_db()
        self.assertEqual(result.work_focus_snapshot, acknowledgement['focus'])
        self.assertEqual(
            result.turn.trusted_context[WORK_FOCUS_SNAPSHOT_KEY],
            acknowledgement['focus'],
        )
        self.assertNotIn('receipt', result.turn.trusted_context)
        saved = self.repository.get_turn_work_focus(self.thread.pk, result.turn.pk)
        self.assertEqual(saved['focus'], acknowledgement['focus'])
        self.assertFalse(saved['stale'])

    def test_exact_focus_replays_without_another_turn(self):
        """Reissuing a receipt does not change the canonical focus identity."""
        first = self.begin(self.acknowledge()['receipt'])
        replay = self.begin(self.acknowledge()['receipt'])
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.turn.pk, first.turn.pk)
        self.assertEqual(replay.work_focus_snapshot, first.work_focus_snapshot)
        self.assertEqual(ChatTurn.objects.count(), 1)

    def test_changed_focus_conflicts_even_with_same_caller_fingerprint(self):
        """The repository independently prevents accidental intent rebinding."""
        self.begin(self.acknowledge()['receipt'])
        with self.assertRaises(IdempotencyConflict):
            self.begin(self.acknowledge(self.second_work)['receipt'])
        self.assertEqual(ChatTurn.objects.count(), 1)

    def test_focus_cannot_be_removed_on_replay(self):
        """A focused request cannot silently replay as an ordinary request."""
        self.begin(self.acknowledge()['receipt'])
        with self.assertRaises(IdempotencyConflict):
            self.begin()

    def test_focus_cannot_be_added_to_an_unfocused_replay(self):
        """An ordinary saved request cannot acquire a new focus retroactively."""
        self.begin()
        with self.assertRaises(IdempotencyConflict):
            self.begin(self.acknowledge()['receipt'])

    def test_unfocused_turns_keep_their_existing_stored_shape(self):
        """No receipt means no new stored keys or implicit record selection."""
        result = self.begin()
        self.assertIsNone(result.work_focus_snapshot)
        self.assertEqual(result.turn.trusted_context, {'actor': str(self.actor.pk)})
        self.assertIsNone(
            self.repository.get_turn_work_focus(self.thread.pk, result.turn.pk)
        )

    def test_unverified_context_cannot_inject_a_snapshot(self):
        """Only the dedicated receipt path writes the server-owned snapshot."""
        with self.assertRaises(InvalidBoundary):
            self.begin(
                trusted_context={
                    WORK_FOCUS_SNAPSHOT_KEY: {'work_order_id': self.work.pk}
                }
            )
        self.assertFalse(ChatTurn.objects.exists())

    def test_tampered_and_malformed_receipts_create_no_turn(self):
        """Signature and shape failures have the same value-free error."""
        receipt = self.acknowledge()['receipt']
        for invalid in [receipt + 'tampered', '', 'invalid', 1, {}, 'a' * 4097]:
            with (
                self.subTest(receipt_type=type(invalid)),
                self.assertRaises(WorkFocusError),
            ):
                self.begin(invalid)
        self.assertFalse(ChatTurn.objects.exists())

    def test_expired_receipt_cannot_begin_a_turn(self):
        """Signing age is enforced server-side and is not browser-controlled."""
        with mock.patch('django.core.signing.time.time', return_value=1000):
            receipt = self.acknowledge()['receipt']
        with mock.patch('django.core.signing.time.time', return_value=1301):
            with self.assertRaises(WorkFocusError):
                self.begin(receipt)
        self.assertFalse(ChatTurn.objects.exists())

    def test_receipt_cannot_cross_threads_actors_or_server_policy(self):
        """A valid signature alone cannot establish another boundary."""
        receipt = self.acknowledge()['receipt']
        other_thread, _ = self.repository.get_or_create()
        other_repository = ThreadRepository(self.other, 'site:focus')
        other_owned, _ = other_repository.get_or_create()
        for actor, policy, thread in [
            (self.actor, 'site:focus', other_thread),
            (self.other, 'site:focus', other_owned),
            (self.actor, 'other-policy', other_thread),
        ]:
            with self.subTest(thread=thread.pk):
                repository = ThreadRepository(actor, policy)
                if policy != 'site:focus':
                    thread, _ = repository.get_or_create()
                with self.assertRaises(WorkFocusError):
                    repository.begin_turn(
                        thread.pk,
                        content='Wrong binding',
                        modality=TurnModality.TEXT,
                        trusted_context={},
                        modality_metadata={},
                        idempotency_key='wrong-binding',
                        request_fingerprint='b' * 64,
                        correlation_id='wrong-binding',
                        work_focus_receipt=receipt,
                    )
        self.assertFalse(ChatTurn.objects.exists())

    def test_revoked_client_grant_rejects_before_new_turn_and_replay(self):
        """A previous acknowledgement or saved turn grants no current access."""
        receipt = self.acknowledge()['receipt']
        self.begin(receipt)
        self.grant.delete()
        for key in ['focus-turn', 'another-turn']:
            with self.subTest(key=key), self.assertRaises(WorkFocusError):
                self.begin(receipt, key=key)
        self.assertEqual(ChatTurn.objects.count(), 1)

    def test_role_revocation_after_acknowledgement_rejects_intake(self):
        """Cached actor permission objects cannot authorize a later turn."""
        receipt = self.acknowledge()['receipt']
        self.role.delete()
        with self.assertRaises(WorkFocusError):
            self.begin(receipt)
        self.assertFalse(ChatTurn.objects.exists())

    def test_disabled_actor_cannot_acknowledge_or_use_receipt(self):
        """The native thread-write gate also reads live account state."""
        receipt = self.acknowledge()['receipt']
        get_user_model().objects.filter(pk=self.actor.pk).update(is_active=False)
        with self.assertRaises(ThreadNotFound):
            self.acknowledge()
        with self.assertRaises(ThreadNotFound):
            self.begin(receipt)

    def test_changed_lifecycle_or_machine_invalidates_receipt(self):
        """Intake does not quietly adopt a different record version or asset."""
        receipt = self.acknowledge()['receipt']
        WorkOrder.objects.filter(pk=self.work.pk).update(lifecycle_version=2)
        with self.assertRaises(WorkFocusError):
            self.begin(receipt)
        WorkOrder.objects.filter(pk=self.work.pk).update(
            lifecycle_version=1, machine=None
        )
        with self.assertRaises(WorkFocusError):
            self.begin(receipt)
        self.assertFalse(ChatTurn.objects.exists())

    def test_changed_boundary_invalidates_receipt_even_when_both_are_authorized(self):
        """A valid new client grant cannot silently retarget an old identity."""
        receipt = self.acknowledge()['receipt']
        second_client = Client.objects.create(
            name='Second allowed', code='focus-second'
        )
        ClientScopeGrant.objects.create(user=self.actor, client=second_client)
        AssetMachine.objects.filter(pk=self.machine.pk).update(client=second_client)
        with self.assertRaises(WorkFocusError):
            self.begin(receipt)
        self.assertFalse(ChatTurn.objects.exists())

    def test_result_focus_keeps_historical_version_and_marks_staleness(self):
        """A saved result reports its own version, never a rewritten current one."""
        result = self.begin(self.acknowledge()['receipt'])
        WorkOrder.objects.filter(pk=self.work.pk).update(lifecycle_version=2)
        saved = self.repository.get_turn_work_focus(self.thread.pk, result.turn.pk)
        self.assertEqual(saved['focus']['lifecycle_version'], 1)
        self.assertEqual(saved['turn_id'], result.turn.pk)
        self.assertTrue(saved['stale'])

    def test_result_focus_reauthorizes_native_work_and_thread(self):
        """Turn identity cannot be used as a cross-owner or revoked-access oracle."""
        result = self.begin(self.acknowledge()['receipt'])
        other_repository = ThreadRepository(self.other, 'site:focus')
        with self.assertRaises(ThreadNotFound):
            other_repository.get_turn_work_focus(self.thread.pk, result.turn.pk)
        self.grant.delete()
        with self.assertRaises(WorkFocusError):
            self.repository.get_turn_work_focus(self.thread.pk, result.turn.pk)

    def test_unknown_or_wrong_thread_turn_is_not_found(self):
        """Lookup applies the thread boundary before selecting a turn."""
        result = self.begin(self.acknowledge()['receipt'])
        other_thread, _ = self.repository.get_or_create()
        for thread, turn_id in [
            (self.thread, 'absent'),
            (other_thread, result.turn.pk),
        ]:
            with self.subTest(turn=turn_id), self.assertRaises(ThreadNotFound):
                self.repository.get_turn_work_focus(thread.pk, turn_id)

    def test_foreign_signing_salt_is_not_accepted(self):
        """Other signed application tokens cannot be promoted into Work focus."""
        receipt = signing.dumps({'work_order_id': self.work.pk}, salt='other-purpose')
        with self.assertRaises(WorkFocusError):
            self.begin(receipt)

    def test_analysis_and_work_focus_snapshots_remain_separate(self):
        """Work focus neither overwrites nor silently broadens asset scope."""
        self.repository.set_scope(
            self.thread.pk, {'mode': 'all_authorized_assets'}, expected_version=0
        )
        result = self.begin(self.acknowledge()['receipt'])
        self.assertEqual(result.scope_snapshot['version'], 1)
        self.assertEqual(
            result.scope_snapshot['scope']['mode'], 'all_authorized_assets'
        )
        self.assertEqual(result.work_focus_snapshot['work_order_id'], self.work.pk)
        self.assertNotIn('work_order_id', result.scope_snapshot['scope'])

    def test_deleted_work_rejects_receipt_and_saved_focus_read(self):
        """A durable pointer cannot authorize an orphaned native record."""
        receipt = self.acknowledge()['receipt']
        result = self.begin(receipt)
        self.work.delete()
        with self.assertRaises(WorkFocusError):
            self.begin(receipt, key='deleted-work')
        with self.assertRaises(WorkFocusError):
            self.repository.get_turn_work_focus(self.thread.pk, result.turn.pk)

    def test_pre_check_verifies_without_writing_or_locking(self):
        """The HTTP pre-check reports the live identity and creates nothing."""
        receipt = self.acknowledge()['receipt']
        focus = self.repository.verify_work_focus(self.thread.pk, receipt)
        self.assertEqual(focus['work_order_id'], self.work.pk)
        self.assertEqual(focus['lifecycle_version'], self.work.lifecycle_version)
        self.assertFalse(ChatTurn.objects.exists())

    def test_pre_check_is_owner_only_and_refuses_what_intake_refuses(self):
        """The detector cannot be weaker than the intake check it precedes."""
        receipt = self.acknowledge()['receipt']
        with self.assertRaises(ThreadNotFound):
            ThreadRepository(self.other, 'site:focus').verify_work_focus(
                self.thread.pk, receipt
            )
        other_thread, _ = self.repository.get_or_create()
        with self.assertRaises(WorkFocusError):
            self.repository.verify_work_focus(other_thread.pk, receipt)
        self.grant.delete()
        with self.assertRaises(WorkFocusError):
            self.repository.verify_work_focus(self.thread.pk, receipt)
        self.assertFalse(ChatTurn.objects.exists())

    def test_public_projection_carries_ids_but_not_the_boundary(self):
        """Answers and the model get the record ids; the hash stays server-side."""
        focus = self.acknowledge()['focus']
        projected = public_focus(focus)
        self.assertEqual(
            projected,
            {
                'schema_version': 1,
                'kind': 'work_order',
                'work_order_id': self.work.pk,
                'lifecycle_version': self.work.lifecycle_version,
                'machine_id': self.machine.pk,
            },
        )
        self.assertIsNone(public_focus(None))
        self.assertIsNone(public_focus('not a snapshot'))

    def answer(self, work, key):
        """A completed focused turn; returns its answer message id."""
        turn = self.begin(self.acknowledge(work)['receipt'], key=key).turn
        self.repository.terminal(
            turn.pk,
            state=TurnState.COMPLETE,
            canonical_result={'message': 'answer'},
            output_content=f'Answer about {work.pk}',
        )
        turn.refresh_from_db()
        return turn.output_message_id

    def test_reloaded_answers_report_how_their_work_stands_now(self):
        """Current, changed and withdrawn Works read back differently."""
        unchanged = self.answer(self.work, 'unchanged')
        changed = self.answer(self.second_work, 'changed')
        third = WorkOrder.objects.create(title='Third work', machine=self.machine)
        withdrawn = self.answer(third, 'withdrawn')
        unfocused_turn = self.begin(key='unfocused').turn
        self.repository.terminal(
            unfocused_turn.pk,
            state=TurnState.COMPLETE,
            canonical_result={'message': 'plain'},
            output_content='Plain answer',
        )
        unfocused_turn.refresh_from_db()

        WorkOrder.objects.filter(pk=self.second_work.pk).update(
            lifecycle_version=self.second_work.lifecycle_version + 1
        )
        third.customer = Company.objects.create(name='Later owner', is_customer=True)
        third.save(update_fields=['customer'])

        ids = [
            unchanged,
            changed,
            withdrawn,
            unfocused_turn.output_message_id,
            'msg_unknown',
        ]
        self.assertEqual(
            self.repository.work_focus_states(self.thread.pk, ids),
            {unchanged: 'current', changed: 'changed', withdrawn: 'unavailable'},
        )

    def test_state_lookup_checks_each_work_once(self):
        """Two answers about one Work cost one live check, not two."""
        first = self.answer(self.work, 'first')
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as one:
            self.repository.work_focus_states(self.thread.pk, [first])
        second = self.answer(self.work, 'second')
        with CaptureQueriesContext(connection) as two:
            states = self.repository.work_focus_states(self.thread.pk, [first, second])
        self.assertEqual(states, {first: 'current', second: 'current'})
        self.assertEqual(len(two), len(one))

    def test_state_lookup_is_owner_only_and_reauthorizes_the_reader(self):
        """A same-client reader cannot read another owner's answer states."""
        answer = self.answer(self.work, 'owned')
        with self.assertRaises(ThreadNotFound):
            ThreadRepository(self.other, 'site:focus').work_focus_states(
                self.thread.pk, [answer]
            )
        self.role.can_view = False
        self.role.save()
        self.assertEqual(
            self.repository.work_focus_states(self.thread.pk, [answer]),
            {answer: 'unavailable'},
        )
        self.assertEqual(self.repository.work_focus_states(self.thread.pk, []), {})
