"""The A9 sweeper notifications: deterministic, idempotent, fail-soft.

The proposal rail previously went silent after confirmation: nothing told the
user their action executed, failed, or was about to expire. These messages
are server-authored (no model), keyed by proposal id + kind in message
metadata, and must never duplicate under repeated sweeps nor crash the sweep
when a thread is missing or foreign.
"""

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import timedelta
from threading import Event, Lock
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

from django.contrib.auth import get_user_model
from django.db import OperationalError, close_old_connections
from django.test import (
    TestCase,
    TransactionTestCase,
    override_settings,
    skipUnlessDBFeature,
)
from django.utils import timezone

from aichat.models import (
    ChatActionProposal,
    ChatMessage,
    ProposalState,
    ThreadNamespace,
)
from aichat.services import proposals as proposal_service
from aichat.services.proposals import (
    NOTIFICATION_KIND_OUTCOME,
    NOTIFICATION_KIND_WARNING,
    _command_receipt,
    _deliver_notification,
    sweep_proposal_notifications,
)
from aichat.services.threads import ThreadRepository
from aichat.services.voice_bridge import VOICE_PROPOSAL_EXPIRY_SECONDS
from aichat.tasks import expire_stale_chat_action_proposals
from InvenTree.tasks import tasks as scheduled_tasks


def _proposal(owner, thread_id: str, *, state, suffix: str, **overrides):
    defaults = {
        'owner': owner,
        'scope_key': 'site:test',
        'scope_hash': 'a' * 64,
        'thread_id': thread_id,
        'action_type': 'work_order.schedule',
        'target_work_order_id': 123,
        'state': state,
        'policy_version': '1',
        'idempotency_key': f'notif-{suffix}',
        'expires_at': timezone.now() + timedelta(minutes=30),
    }
    defaults.update(overrides)
    return ChatActionProposal.objects.create(**defaults)


def _approval(owner, *, suffix: str):
    """Create the global approval that owns one bridged chat proposal."""
    from approvals.models import ActionType, Approval

    return Approval.objects.create(
        action_type=ActionType.REPAIR_WORK_PACKAGE,
        summary='Create a repair work package',
        payload={'actor_id': owner.pk},
        agent_run_id=f'notification-{suffix}',
        agent_checkpoint_id=f'notification-{suffix}',
        tool_call_id=f'notification-{suffix}',
        idempotency_key=f'notification-{suffix}',
        assigned_to_user=owner,
    )


class ProposalNotificationSweepTests(TestCase):
    """sweep_proposal_notifications counts, idempotency, and boundaries."""

    @classmethod
    def setUpTestData(cls):
        """One owner with a real drawer thread, one stranger."""
        cls.owner = get_user_model().objects.create_user(
            username='sweep-owner', email='sw@example.com', password='pw'
        )
        cls.stranger = get_user_model().objects.create_user(
            username='sweep-stranger', email='st@example.com', password='pw'
        )
        repository = ThreadRepository(cls.owner, 'site:test')
        cls.thread = repository.get_or_create(None, title='Sweep thread')[0]
        # The scoped namespace is gone (S14c); this stale identifier proves
        # delivery fails closed instead of resolving into the main namespace.
        cls.stale_scoped_thread_id = 'scoped_thread_0000stale0000'

    def _messages(self, kind):
        return ChatMessage.objects.filter(
            thread_id=self.thread.pk, metadata__kind=kind
        ).order_by('sequence')

    def test_expiring_proposal_gets_exactly_one_warning(self):
        """A proposal inside the warning window is reminded once, ever."""
        _proposal(
            self.owner,
            self.thread.pk,
            state=ProposalState.PROPOSED,
            suffix='warn',
            expires_at=timezone.now() + timedelta(minutes=10),
        )
        first = sweep_proposal_notifications()
        second = sweep_proposal_notifications()
        self.assertEqual(first['warned'], 1)
        self.assertEqual(second['warned'], 0)
        warnings = self._messages(NOTIFICATION_KIND_WARNING)
        self.assertEqual(warnings.count(), 1)
        self.assertIn('expires at', warnings[0].content)
        self.assertIn('work order 123', warnings[0].content)

    def test_proposal_outside_the_window_is_not_warned(self):
        """A fresh proposal with a distant expiry stays quiet."""
        _proposal(
            self.owner,
            self.thread.pk,
            state=ProposalState.PROPOSED,
            suffix='fresh',
            expires_at=timezone.now() + timedelta(hours=6),
        )
        self.assertEqual(sweep_proposal_notifications()['warned'], 0)

    @override_settings(AIMMS_APPROVAL_QUEUE_OWNS_REPAIRS=True)
    def test_approval_bridged_proposal_is_not_warned_on_the_chat_rail(self):
        """A chat reminder must not offer a deadline this rail cannot decide."""
        proposal = _proposal(
            self.owner,
            self.thread.pk,
            state=ProposalState.PROPOSED,
            suffix='approval-warning',
            action_type='repair_work_package.create',
            target_work_order_id=None,
            approval=_approval(self.owner, suffix='approval-warning'),
            expires_at=timezone.now() + timedelta(minutes=10),
        )

        with (
            patch.object(proposal_service, '_require_role'),
            self.assertRaises(proposal_service.ApprovalOwnsExecution),
        ):
            proposal_service.confirm_proposal(
                owner=self.owner,
                scope_hash=proposal.scope_hash,
                proposal_id=proposal.id,
            )

        self.assertEqual(sweep_proposal_notifications()['warned'], 0)
        self.assertFalse(
            ChatMessage.objects.filter(
                thread=self.thread,
                metadata__proposal_id=str(proposal.id),
                metadata__kind=NOTIFICATION_KIND_WARNING,
            ).exists()
        )

    @override_settings(AIMMS_APPROVAL_QUEUE_OWNS_REPAIRS=True)
    def test_approval_bridged_row_is_not_a_chat_outcome_candidate(self):
        """Approval outcomes stay silent until a real synchronization path exists."""
        proposal = _proposal(
            self.owner,
            self.thread.pk,
            state=ProposalState.EXECUTED,
            suffix='approval-outcome',
            action_type='repair_work_package.create',
            target_work_order_id=None,
            approval=_approval(self.owner, suffix='approval-outcome'),
            receipt={'command': 'create_repair_work_package'},
        )

        self.assertEqual(sweep_proposal_notifications()['outcomes'], 0)
        self.assertFalse(
            ChatMessage.objects.filter(
                thread=self.thread,
                metadata__proposal_id=str(proposal.id),
                metadata__kind=NOTIFICATION_KIND_OUTCOME,
            ).exists()
        )

    def test_executed_proposal_gets_one_outcome_message_with_real_receipt_detail(self):
        """Dispatch outcome lands once, carrying detail from a real receipt."""
        receipt = _command_receipt(
            SimpleNamespace(
                work_order_id=123,
                event_id=41,
                command='schedule',
                lifecycle_status='planned',
                lifecycle_version=7,
                correlation_id=UUID('00000000-0000-0000-0000-000000000041'),
                idempotency_key='proposal:real-schedule-receipt',
                metadata={
                    'scheduled_start': '2026-08-05T09:00:00+00:00',
                    'scheduled_end': '2026-08-05T10:30:00+00:00',
                },
            )
        )
        _proposal(
            self.owner,
            self.thread.pk,
            state=ProposalState.EXECUTED,
            suffix='done',
            receipt=receipt,
        )
        first = sweep_proposal_notifications()
        second = sweep_proposal_notifications()
        self.assertEqual(first['outcomes'], 1)
        self.assertEqual(second['outcomes'], 0)
        outcomes = self._messages(NOTIFICATION_KIND_OUTCOME)
        self.assertEqual(outcomes.count(), 1)
        self.assertIn('Applied', outcomes[0].content)
        self.assertIn('2026-08-05T09:00:00+00:00', outcomes[0].content)
        self.assertIn('2026-08-05T10:30:00+00:00', outcomes[0].content)

    def test_legacy_schedule_receipt_does_not_claim_the_window_was_cleared(self):
        """Missing historical metadata means unknown detail, not a null window."""
        receipt = _command_receipt(
            SimpleNamespace(
                work_order_id=123,
                event_id=42,
                command='schedule',
                lifecycle_status='planned',
                lifecycle_version=8,
                correlation_id=UUID('00000000-0000-0000-0000-000000000042'),
                idempotency_key='proposal:legacy-schedule-receipt',
                metadata=None,
            )
        )
        _proposal(
            self.owner,
            self.thread.pk,
            state=ProposalState.EXECUTED,
            suffix='legacy-schedule',
            receipt=receipt,
        )

        self.assertEqual(sweep_proposal_notifications()['outcomes'], 1)
        outcome = self._messages(NOTIFICATION_KIND_OUTCOME).get()
        self.assertIn('Applied', outcome.content)
        self.assertNotIn('window was cleared', outcome.content)

    def test_stale_scoped_thread_id_fails_closed(self):
        """A proposal naming a scoped_ id delivers nothing after S14c.

        The scoped rail is gone; its reserved prefix must refuse rather than
        deliver a notification into the main namespace.
        """
        proposal = _proposal(
            self.owner,
            self.stale_scoped_thread_id,
            state=ProposalState.EXECUTED,
            suffix='scoped-done',
            receipt={'command': 'hold', 'lifecycle_status': 'on_hold'},
        )

        self.assertEqual(sweep_proposal_notifications()['outcomes'], 0)
        self.assertFalse(
            ChatMessage.objects.filter(
                metadata__proposal_id=str(proposal.id)
            ).exists()
        )

    def test_stale_warning_candidate_is_rechecked_under_lock(self):
        """A concurrent rejection must not receive the scan's obsolete reminder."""
        proposal = _proposal(
            self.owner,
            self.thread.pk,
            state=ProposalState.PROPOSED,
            suffix='stale-warning',
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        ChatActionProposal.objects.filter(pk=proposal.pk).update(
            state=ProposalState.REJECTED
        )

        delivered = _deliver_notification(
            proposal, NOTIFICATION_KIND_WARNING, 'Obsolete reminder.'
        )

        self.assertFalse(delivered)
        self.assertFalse(
            ChatMessage.objects.filter(
                thread=self.thread,
                metadata__proposal_id=str(proposal.id),
                metadata__kind=NOTIFICATION_KIND_WARNING,
            ).exists()
        )

    def test_command_receipt_whitelists_notification_metadata(self):
        """Unrelated command metadata must not silently expand durable receipts."""
        receipt = _command_receipt(
            SimpleNamespace(
                work_order_id=123,
                event_id=43,
                command='hold',
                lifecycle_status='on_hold',
                lifecycle_version=9,
                correlation_id=UUID('00000000-0000-0000-0000-000000000043'),
                idempotency_key='proposal:metadata-whitelist',
                metadata={'internal_note': 'must not persist here'},
            )
        )
        self.assertNotIn('result_metadata', receipt)

    def test_failed_proposal_reports_the_failure_code(self):
        """A failed dispatch says so, names the code, and asserts no change."""
        _proposal(
            self.owner,
            self.thread.pk,
            state=ProposalState.FAILED,
            suffix='fail',
            failure_code='VERSION_CONFLICT',
        )
        self.assertEqual(sweep_proposal_notifications()['outcomes'], 1)
        outcome = self._messages(NOTIFICATION_KIND_OUTCOME).get()
        self.assertIn('VERSION_CONFLICT', outcome.content)
        self.assertIn('Nothing was changed', outcome.content)

    def test_foreign_or_missing_threads_fail_soft(self):
        """A stranger-owned or nonexistent thread skips without crashing."""
        _proposal(
            self.stranger,
            self.thread.pk,  # owner mismatch: stranger cannot write here
            state=ProposalState.EXECUTED,
            suffix='foreign',
            receipt={'summary': 'x'},
        )
        _proposal(
            self.owner,
            'thread_does_not_exist',
            state=ProposalState.EXECUTED,
            suffix='missing',
            receipt={'summary': 'y'},
        )
        counts = sweep_proposal_notifications()
        self.assertEqual(counts['outcomes'], 0)
        self.assertEqual(self._messages(NOTIFICATION_KIND_OUTCOME).count(), 0)

    def test_deleted_thread_id_is_not_rebound_to_old_proposals(self):
        """Reusing a deleted id must not inject old notices into a new transcript."""
        warning = _proposal(
            self.owner,
            self.thread.pk,
            state=ProposalState.PROPOSED,
            suffix='deleted-thread-warning',
            expires_at=timezone.now() + timedelta(minutes=5),
        )
        outcome = _proposal(
            self.owner,
            self.thread.pk,
            state=ProposalState.EXECUTED,
            suffix='deleted-thread-outcome',
            receipt={'command': 'hold', 'lifecycle_status': 'on_hold'},
        )
        repository = ThreadRepository(self.owner, 'site:test')
        repository.delete(self.thread.pk)
        replacement, created = repository.get_or_create(
            self.thread.pk, title='Unrelated replacement transcript'
        )
        self.assertTrue(created)
        self.assertGreater(replacement.created_at, warning.created_at)
        self.assertGreater(replacement.created_at, outcome.created_at)

        self.assertEqual(sweep_proposal_notifications(), {'warned': 0, 'outcomes': 0})
        self.assertFalse(ChatMessage.objects.filter(thread=replacement).exists())

    def test_repository_database_failure_is_row_local(self):
        """One transient notification write cannot abort all later outcomes."""
        _proposal(
            self.owner,
            self.thread.pk,
            state=ProposalState.EXECUTED,
            suffix='database-error-one',
            receipt={'command': 'hold', 'lifecycle_status': 'on_hold'},
        )
        _proposal(
            self.owner,
            self.thread.pk,
            state=ProposalState.FAILED,
            suffix='database-error-two',
            failure_code='STALE_VERSION',
        )
        original_append = ThreadRepository.append
        calls = 0

        def flaky_append(repository, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OperationalError('temporary notification failure')
            return original_append(repository, *args, **kwargs)

        with patch.object(
            ThreadRepository, 'append', autospec=True, side_effect=flaky_append
        ):
            counts = sweep_proposal_notifications()

        self.assertEqual(counts['outcomes'], 1)
        self.assertEqual(self._messages(NOTIFICATION_KIND_OUTCOME).count(), 1)

    def test_notification_bug_does_not_block_expiry(self):
        """Expiry is the mandatory half of the task; notification is fail-soft."""
        stale = _proposal(
            self.owner,
            self.thread.pk,
            state=ProposalState.PROPOSED,
            suffix='must-expire',
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        with (
            patch.object(
                proposal_service,
                'sweep_proposal_notifications',
                side_effect=RuntimeError('notification bug'),
            ),
            patch('aichat.tasks.logger.exception') as logged,
        ):
            expire_stale_chat_action_proposals()

        logged.assert_called_once()
        stale.refresh_from_db()
        self.assertEqual(stale.state, ProposalState.EXPIRED)

    def test_sweep_cadence_covers_the_shortest_proposal_ttl(self):
        """A three-minute voice proposal always has a scheduled sweep in time."""
        registrations = [
            task
            for task in scheduled_tasks.task_list
            if task.func is expire_stale_chat_action_proposals
        ]
        self.assertEqual(len(registrations), 1)
        self.assertLessEqual(
            registrations[0].minutes, VOICE_PROPOSAL_EXPIRY_SECONDS // 60
        )


@skipUnlessDBFeature('has_select_for_update')
class ProposalNotificationConcurrencyTests(TransactionTestCase):
    """Production row locking makes notification deduplication atomic."""

    reset_sequences = True

    def setUp(self):
        """Create one outcome proposal on a durable unscoped thread."""
        self.owner = get_user_model().objects.create_user(
            username='notification-lock-owner'
        )
        self.thread = ThreadRepository(self.owner.pk, 'site:test').get_or_create()[0]
        self.proposal = _proposal(
            self.owner,
            self.thread.pk,
            state=ProposalState.EXECUTED,
            suffix='concurrent-outcome',
            receipt={'command': 'hold', 'lifecycle_status': 'on_hold'},
        )

    def test_two_sweepers_append_one_outcome(self):
        """A second worker cannot pass the existence check before the first commits."""
        first_checked = Event()
        second_checked = Event()
        release_first = Event()
        call_lock = Lock()
        check_count = 0
        original_exists = proposal_service._notification_exists

        def gated_exists(*args, **kwargs):
            nonlocal check_count
            result = original_exists(*args, **kwargs)
            with call_lock:
                index = check_count
                check_count += 1
            if index == 0:
                first_checked.set()
                release_first.wait(timeout=5)
            else:
                second_checked.set()
            return result

        def deliver():
            close_old_connections()
            try:
                proposal = ChatActionProposal.objects.select_related('owner').get(
                    pk=self.proposal.pk
                )
                return _deliver_notification(
                    proposal, NOTIFICATION_KIND_OUTCOME, 'Applied once.'
                )
            finally:
                close_old_connections()

        with (
            patch.object(
                proposal_service, '_notification_exists', side_effect=gated_exists
            ),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            first = executor.submit(deliver)
            self.assertTrue(first_checked.wait(timeout=5))
            second = executor.submit(deliver)
            # With the proposal row locked, worker two cannot reach the check
            # until worker one has appended and committed.
            self.assertFalse(second_checked.wait(timeout=0.3))
            release_first.set()
            results = [first.result(timeout=5), second.result(timeout=5)]

        self.assertEqual(sorted(results), [False, True])
        self.assertEqual(
            ChatMessage.objects.filter(
                thread=self.thread,
                metadata__proposal_id=str(self.proposal.id),
                metadata__kind=NOTIFICATION_KIND_OUTCOME,
            ).count(),
            1,
        )

    def test_delete_recreate_waits_for_the_notification_thread_lock(self):
        """Deletion cannot swap generations between notification check and append."""
        checked = Event()
        release_delivery = Event()
        original_exists = proposal_service._notification_exists

        def gated_exists(*args, **kwargs):
            checked.set()
            release_delivery.wait(timeout=5)
            return original_exists(*args, **kwargs)

        def deliver():
            close_old_connections()
            try:
                proposal = ChatActionProposal.objects.select_related('owner').get(
                    pk=self.proposal.pk
                )
                return _deliver_notification(
                    proposal, NOTIFICATION_KIND_OUTCOME, 'Applied to the old thread.'
                )
            finally:
                close_old_connections()

        def replace_thread():
            close_old_connections()
            try:
                repository = ThreadRepository(self.owner.pk, 'site:test')
                repository.delete(self.thread.pk)
                replacement, created = repository.get_or_create(self.thread.pk)
                return replacement.pk, replacement.created_at, created
            finally:
                close_old_connections()

        with (
            patch.object(
                proposal_service, '_notification_exists', side_effect=gated_exists
            ),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            delivery = executor.submit(deliver)
            self.assertTrue(checked.wait(timeout=5))
            replacement = executor.submit(replace_thread)
            try:
                # Delivery has already locked the matched transcript, so delete
                # cannot replace it while the dedupe/append boundary is open.
                with self.assertRaises(FutureTimeoutError):
                    replacement.result(timeout=0.3)
            finally:
                release_delivery.set()
            self.assertTrue(delivery.result(timeout=5))
            thread_id, created_at, created = replacement.result(timeout=5)

        self.assertTrue(created)
        self.assertGreater(created_at, self.proposal.created_at)
        self.assertFalse(ChatMessage.objects.filter(thread_id=thread_id).exists())
