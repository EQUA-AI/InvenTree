"""The A9 sweeper notifications: deterministic, idempotent, fail-soft.

The proposal rail previously went silent after confirmation: nothing told the
user their action executed, failed, or was about to expire. These messages
are server-authored (no model), keyed by proposal id + kind in message
metadata, and must never duplicate under repeated sweeps nor crash the sweep
when a thread is missing or foreign.
"""

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from aichat.models import ChatActionProposal, ChatMessage, ProposalState
from aichat.services.proposals import (
    NOTIFICATION_KIND_OUTCOME,
    NOTIFICATION_KIND_WARNING,
    sweep_proposal_notifications,
)
from aichat.services.threads import ThreadRepository


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

    def test_executed_proposal_gets_one_outcome_message_with_receipt_summary(self):
        """Dispatch outcome lands once, carrying the receipt summary text."""
        _proposal(
            self.owner,
            self.thread.pk,
            state=ProposalState.EXECUTED,
            suffix='done',
            receipt={'summary': 'WO-123 now scheduled Tue 09:00.'},
        )
        first = sweep_proposal_notifications()
        second = sweep_proposal_notifications()
        self.assertEqual(first['outcomes'], 1)
        self.assertEqual(second['outcomes'], 0)
        outcomes = self._messages(NOTIFICATION_KIND_OUTCOME)
        self.assertEqual(outcomes.count(), 1)
        self.assertIn('Applied', outcomes[0].content)
        self.assertIn('WO-123 now scheduled Tue 09:00.', outcomes[0].content)

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
