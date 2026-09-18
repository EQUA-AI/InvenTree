"""Durable provider jobs: lease, version and retry qualification cases."""

import uuid
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from aichat.models import MemoryFact, MemoryFactJob
from aichat.services import memory_fact_jobs as jobs


class MemoryFactJobTests(TestCase):
    """No providers or live queue; verify persistence and non-revival boundaries."""

    def setUp(self):
        """Create an unconfirmed preference and a claimed shield job."""
        owner = get_user_model().objects.create_user(username='fact-job-owner')
        self.fact = MemoryFact.objects.create(
            owner=owner,
            entity_kind='user',
            entity_id=str(owner.pk),
            slot_key='checklist',
            memory_type='user_preference',
            text='I prefer a maintenance checklist.',
            text_lang='en',
            origin='compaction',
            origin_modality='chat',
            visibility_scope='user_private',
            classification='preference',
            notice_version='memory-v1',
            claim_fingerprint='a' * 64,
        )
        self.job = MemoryFactJob.objects.create(
            fact=self.fact,
            fact_version=1,
            kind='shield',
            state='claimed',
            lease_token=uuid.uuid4(),
            claimed_at=timezone.now(),
            attempts=1,
        )
        self.admission = self.enterContext(
            mock.patch.object(jobs, 'eligible', return_value=True)
        )

    def test_stale_lease_cannot_annotate(self):
        """A recovered task's previous result cannot change the confirmation view."""
        MemoryFactJob.objects.filter(pk=self.job.pk).update(lease_token=uuid.uuid4())
        self.assertEqual(jobs.finish(self.job, shield='clear'), 'stale')
        self.fact.refresh_from_db()
        self.assertEqual(self.fact.version, 1)
        self.assertEqual(self.fact.shield_state, 'pending')

    def test_revoked_eligibility_discards_result(self):
        """Consent lost during the call suppresses both shield and vector writes."""
        self.admission.return_value = False
        self.assertEqual(jobs.finish(self.job, shield='clear'), 'skipped')
        self.fact.refresh_from_db()
        self.assertEqual(self.fact.shield_state, 'pending')

    def test_success_requires_a_new_preview_version(self):
        """A changed shield verdict cannot be confirmed using an old preview hash."""
        self.assertEqual(jobs.finish(self.job, shield='flagged'), 'complete')
        self.fact.refresh_from_db()
        self.assertEqual(self.fact.version, 2)
        self.assertTrue(self.fact.injection_flag)
        self.assertEqual(self.fact.lifecycle_state, 'proposed')

    def test_failed_third_attempt_stays_terminal(self):
        """No fourth provider attempt is admitted for this fact version."""
        MemoryFactJob.objects.filter(pk=self.job.pk).update(attempts=3)
        self.assertEqual(jobs.finish(self.job), 'failed')
        self.assertIsNone(jobs.acquire(self.job.pk))

    def test_budget_deferral_does_not_spend_an_attempt(self):
        """No-call quota deferrals can resume after the budget window changes."""
        self.assertEqual(jobs.finish(self.job, budget_deferred=True), 'deferred')
        self.job.refresh_from_db()
        self.assertEqual(self.job.attempts, 0)
        self.assertIsNone(jobs.acquire(self.job.pk))

    def test_republication_makes_deferred_job_executable(self):
        """The future publication lease must not cause immediate self-deferral."""
        MemoryFactJob.objects.filter(pk=self.job.pk).update(
            state='deferred', lease_token=None
        )
        with (
            mock.patch.object(jobs, 'enabled', return_value=True),
            mock.patch.object(
                jobs.memory_worker,
                'worker_status',
                return_value={'backpressured': False, 'heartbeat_fresh': True},
            ),
            mock.patch.object(
                jobs.memory_worker, 'cluster_config', return_value={'timeout': 300}
            ),
            mock.patch.object(
                jobs.memory_worker, '_broker', return_value=SimpleNamespace()
            ),
            mock.patch.object(
                jobs.memory_worker, 'async_task', return_value='task'
            ) as publish,
        ):
            self.assertTrue(jobs.publish(self.job.pk))
            self.assertEqual(publish.call_args.kwargs['cluster'], 'ai-memory')
            self.assertFalse(publish.call_args.kwargs['sync'])
        acquired = jobs.acquire(self.job.pk)
        self.assertIsNotNone(acquired)
        self.assertEqual(acquired[1].state, 'claimed')
