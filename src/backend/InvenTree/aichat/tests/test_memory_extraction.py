"""Queue obligations, lease recovery and disabled-path regression cases."""

import uuid
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from aichat.models import ChatMessage, MemoryExtractionClaim, MemoryExtractionRun
from aichat.services import memory_extraction as service
from aichat.services.memory_eligibility import MemoryEligibility
from aichat.services.threads import ThreadRepository


class MemoryExtractionTests(TestCase):
    """Exercise queue state without providers, actual workers or installed schedules."""

    def setUp(self):
        """Prepare one owned source and explicit mocked admission state."""
        owner = get_user_model().objects.create_user(username='extract-owner')
        self.repository = ThreadRepository(actor=owner, scope_key='site:main')
        self.thread, _ = self.repository.get_or_create()
        self.source = ChatMessage.objects.create(
            thread=self.thread,
            sequence=1,
            role='user',
            content='A maintenance checklist would be helpful for future repair instructions.',
        )
        self.thread.next_sequence = 3
        self.thread.save(update_fields=['next_sequence'])
        self.enterContext(mock.patch.object(service, 'enabled', return_value=True))
        self.enterContext(
            mock.patch.object(
                service,
                'get_settings',
                return_value=SimpleNamespace(memory_extraction_deployment='fixture'),
            )
        )
        self.enterContext(
            mock.patch.object(
                service,
                'evaluate_memory_eligibility',
                return_value=MemoryEligibility(
                    'eligible', frozenset({'fixture'}), 'memory-v1'
                ),
            )
        )
        self.enterContext(
            mock.patch.object(
                service.memory_worker, 'cluster_config', return_value={'timeout': 300}
            )
        )

    def test_turn_admission_is_idempotent_and_provider_free(self):
        """A retry records one durable claim for the same finalized sequence."""
        service.enqueue_for_thread(self.thread)
        service.enqueue_for_thread(self.thread)
        self.assertEqual(MemoryExtractionClaim.objects.count(), 1)
        self.assertEqual(MemoryExtractionClaim.objects.get().through_sequence, 2)

    def test_wrong_lease_cannot_commit_watermark(self):
        """An obsolete worker cannot mark another attempt's source window complete."""
        claim = MemoryExtractionClaim.objects.create(
            thread=self.thread,
            through_sequence=2,
            state='claimed',
            lease_token=uuid.uuid4(),
            claimed_at=timezone.now(),
        )
        self.assertFalse(
            service._finish(self.repository, claim.pk, uuid.uuid4(), 2, 'complete', {})
        )
        self.thread.refresh_from_db()
        self.assertEqual(self.thread.memory_through_sequence, 0)
        self.assertFalse(MemoryExtractionRun.objects.exists())

    def test_claim_excludes_second_live_worker(self):
        """Cumulative turn claims cannot process one thread concurrently."""
        first = MemoryExtractionClaim.objects.create(
            thread=self.thread, through_sequence=2
        )
        second = MemoryExtractionClaim.objects.create(
            thread=self.thread, through_sequence=3
        )
        self.assertIsNotNone(service._acquire(first.pk))
        self.assertIsNone(service._acquire(second.pk))
        self.assertIsNone(service._acquire(first.pk))

    def test_publication_reopens_a_due_deferred_claim_for_immediate_execution(self):
        """The queue publication delay is not confused with provider retry delay."""
        claim = MemoryExtractionClaim.objects.create(
            thread=self.thread,
            through_sequence=2,
            state='deferred',
            next_attempt_at=timezone.now(),
        )
        with (
            mock.patch.object(
                service.memory_worker,
                'worker_status',
                return_value={'backpressured': False, 'heartbeat_fresh': True},
            ),
            mock.patch.object(service.memory_worker, '_broker'),
            mock.patch.object(
                service.memory_worker, 'async_task', return_value='fixture'
            ) as queue,
        ):
            self.assertEqual(service.publish(claim.pk)['status'], 'queued')
        claim.refresh_from_db()
        self.assertEqual(claim.state, 'pending')
        self.assertIsNotNone(service._acquire(claim.pk))
        self.assertFalse(queue.call_args.kwargs['sync'])

    def test_failed_window_leaves_a_diagnostic_gap_without_forever_retrying(self):
        """Exhaustion records failure, advances only the bounded window and stops."""
        claim = MemoryExtractionClaim.objects.create(
            thread=self.thread,
            through_sequence=2,
            state='claimed',
            lease_token=uuid.uuid4(),
            claimed_at=timezone.now(),
            attempts=3,
        )
        service._retry(self.repository, claim, 2, {'n_input_messages': 1})
        claim.refresh_from_db()
        self.thread.refresh_from_db()
        self.assertEqual(claim.state, 'failed')
        self.assertEqual(self.thread.memory_through_sequence, 2)
        self.assertEqual(MemoryExtractionRun.objects.get().outcome, 'failed')
