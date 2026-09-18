"""Immediate forgetting and bounded owner-cleanup cases; no providers invoked."""

import os
from datetime import timedelta
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from pydantic import SecretStr

from aichat.models import (
    AIRetentionOutbox,
    ChatThread,
    MemoryFact,
    MemoryFactEvent,
    MemoryFactTombstone,
    UserMemorySettings,
)
from aichat.services import memory_lifecycle as service


class MemoryLifecycleTests(TestCase):
    """Use schema-compatible fixtures without enabling semantic execution."""

    def setUp(self):
        """A reproducible non-production key makes fingerprint tests deterministic."""
        self.owner = get_user_model().objects.create_user(username='forget-owner')
        self.other = get_user_model().objects.create_user(username='forget-other')
        self.thread = ChatThread.objects.create(
            owner=self.owner,
            scope_key='fixture',
            scope_hash='fixture',
            summary='Private summary',
            next_sequence=21,
        )
        self.enterContext(
            mock.patch.object(
                service,
                'get_settings',
                return_value=SimpleNamespace(
                    aimms_memory_fingerprint_key=SecretStr('fixture-key-' * 4)
                ),
            )
        )
        self.enterContext(mock.patch.dict(os.environ, {'INVENTREE_RESTORE_HOLD': '0'}))

    def fact(self, **changes):
        """Superseded fixture carries content but has no recall authority."""
        values = {
            'owner': self.owner,
            'source_thread': self.thread,
            'entity_kind': 'user',
            'entity_id': str(self.owner.pk),
            'slot_key': 'answer_style',
            'memory_type': 'user_preference',
            'text': 'PRIVATE_MEMORY_SENTINEL',
            'text_lang': 'en',
            'origin': 'user_explicit',
            'origin_modality': 'chat',
            'visibility_scope': 'user_private',
            'classification': 'preference',
            'notice_version': 'memory-v1',
            'lifecycle_state': 'superseded',
            'claim_fingerprint': service.claim_fingerprint('PRIVATE_MEMORY_SENTINEL'),
        }
        values.update(changes)
        return MemoryFact.objects.create(**values)

    def test_forget_scrubs_content_leaves_keyed_barrier_and_skips_old_summary_input(
        self,
    ):
        """Retry is idempotent and never needs to retain the claim text."""
        fact = self.fact()
        result = service.forget_fact(self.owner, fact.pk, expected_version=1)
        self.assertEqual(result['status'], 'forgotten')
        fact.refresh_from_db()
        self.thread.refresh_from_db()
        self.assertEqual(fact.text, '')
        self.assertIsNone(fact.embedding)
        self.assertIsNone(fact.canonical_value)
        self.assertEqual(fact.lifecycle_state, 'forgotten')
        self.assertEqual(self.thread.summary, '')
        self.assertEqual(self.thread.summary_through_sequence, 20)
        tombstone = MemoryFactTombstone.objects.get(fact_id=fact.pk)
        self.assertEqual(
            tombstone.claim_fingerprint,
            service.claim_fingerprint('private_memory_sentinel'),
        )
        self.assertFalse(tombstone.residual_pending)
        service.forget_fact(self.owner, fact.pk, expected_version=1)
        self.assertEqual(MemoryFactEvent.objects.filter(action='forget').count(), 1)
        self.assertNotIn(
            'PRIVATE_MEMORY_SENTINEL', str(MemoryFactTombstone.objects.values().first())
        )

    def test_wrong_owner_and_stale_version_do_not_mutate(self):
        """The identifier never widens the authorized owner boundary."""
        fact = self.fact()
        with self.assertRaises(ValueError):
            service.forget_fact(self.other, fact.pk, expected_version=1)
        with self.assertRaises(ValueError):
            service.forget_fact(self.owner, fact.pk, expected_version=2)
        fact.refresh_from_db()
        self.assertEqual(fact.text, 'PRIVATE_MEMORY_SENTINEL')
        self.assertFalse(MemoryFactTombstone.objects.exists())

    def test_bounded_owner_retry_does_not_expand_past_selection_cutoff(self):
        """An earlier deletion obligation cannot erase newly created facts."""
        first = self.fact()
        second = self.fact(slot_key='detail_style')
        cutoff = timezone.now()
        future = self.fact(slot_key='later_style')
        MemoryFact.objects.filter(pk=future.pk).update(
            created_at=cutoff + timedelta(seconds=1)
        )
        reference = service.purge_reference(self.owner.pk, cutoff, 'erasure')
        result = service.forget_owner_facts(self.owner.pk, cutoff=cutoff, limit=1)
        self.assertEqual(result['status'], 'purge_incomplete')
        self.assertEqual(service.owner_purge_residual(reference), 1)
        service.retry_owner_purge(reference)
        self.assertEqual(service.owner_purge_residual(reference), 0)
        self.assertEqual(
            MemoryFact.objects.filter(
                pk__in=[first.pk, second.pk], lifecycle_state='forgotten'
            ).count(),
            2,
        )
        future.refresh_from_db()
        self.assertEqual(future.lifecycle_state, 'superseded')

    def test_opt_out_survives_cleanup_failure_and_has_durable_retry(self):
        """A failing scrub never silently restores permission to learn."""
        self.fact(lifecycle_state='proposed')
        with mock.patch.object(
            service, 'forget_owner_facts', side_effect=RuntimeError('PRIVATE')
        ):
            result = service.set_opt_out(self.owner, opted_out=True)
        self.assertEqual(result, {'opted_out': True, 'status': 'purge_incomplete'})
        self.assertTrue(UserMemorySettings.objects.get(user=self.owner).opted_out)
        row = AIRetentionOutbox.objects.get(kind='memory_owner_purge')
        service.retry_owner_purge(row.reference)
        self.assertEqual(service.owner_purge_residual(row.reference), 0)
        self.assertEqual(MemoryFact.objects.get().lifecycle_state, 'withdrawn')

    def test_mode_off_withdraws_proposals_without_forgetting_retained_facts(self):
        """Extraction intent and confirmed-memory deletion are distinct actions."""
        proposed = self.fact(lifecycle_state='proposed')
        retained = self.fact(slot_key='retained')
        service.withdraw_proposals_for_thread(self.thread)
        proposed.refresh_from_db()
        retained.refresh_from_db()
        self.assertEqual(proposed.lifecycle_state, 'withdrawn')
        self.assertEqual(proposed.text, '')
        self.assertEqual(retained.lifecycle_state, 'superseded')
