"""Authored source-severance cases; no upload, storage or provider calls."""

import os
from types import SimpleNamespace
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from pydantic import SecretStr

from aichat.models import MemoryFact, MemoryFactClaim, MemoryFactTombstone
from aichat.services import attachment_memory, memory_lifecycle


class AttachmentMemoryTests(TestCase):
    """Confirmed knowledge survives source deletion; an unconfirmed claim does not."""

    def setUp(self):
        """Disposable facts use an attachment-id fixture without touching storage."""
        self.owner = get_user_model().objects.create_user(
            username='attachment-memory-owner'
        )
        self.enterContext(mock.patch.dict(os.environ, {'INVENTREE_RESTORE_HOLD': '0'}))
        self.enterContext(
            mock.patch.object(
                memory_lifecycle,
                'get_settings',
                return_value=SimpleNamespace(
                    aimms_memory_fingerprint_key=SecretStr('fixture-key-' * 4)
                ),
            )
        )

    def fact(self, state):
        """Superseded is a retained confirmed lifecycle for this deletion fixture."""
        fact = MemoryFact.objects.create(
            owner=self.owner,
            entity_kind='user',
            entity_id=str(self.owner.pk),
            slot_key=state,
            memory_type='user_preference',
            text='Synthetic maintenance checklist preference',
            text_lang='en',
            origin='compaction',
            origin_modality='chat',
            visibility_scope='user_private',
            classification='preference',
            notice_version='memory-v1',
            lifecycle_state=state,
            claim_fingerprint=memory_lifecycle.claim_fingerprint(
                'Synthetic maintenance checklist preference'
            ),
        )
        MemoryFactClaim.objects.create(
            fact=fact,
            attachment_id=77,
            source_class='user_utterance',
            content_trust='untrusted_fenced',
            source_fingerprint='a' * 64,
        )
        return fact

    def test_proposed_fact_is_withdrawn_with_proof_and_confirmed_fact_is_retained(self):
        """Cleanup is idempotent and clears every attachment pointer."""
        suggestion, retained = self.fact('proposed'), self.fact('superseded')
        report = attachment_memory.cleanup(77, require_complete=True)
        self.assertEqual(report['status'], 'purged')
        suggestion.refresh_from_db()
        retained.refresh_from_db()
        self.assertEqual(suggestion.lifecycle_state, 'withdrawn')
        self.assertEqual(suggestion.text, '')
        self.assertTrue(
            MemoryFactTombstone.objects.filter(fact_id=suggestion.pk).exists()
        )
        self.assertEqual(retained.text, 'Synthetic maintenance checklist preference')
        self.assertEqual(retained.version, 2)
        self.assertEqual(attachment_memory.residual(77), 0)
        self.assertEqual(attachment_memory.cleanup(77)['severed'], 0)

    def test_oversized_atomic_deletion_refuses_before_content_changes(self):
        """A deletion caller cannot remove the source while cleanup remains."""
        first, second = self.fact('proposed'), self.fact('superseded')
        with (
            mock.patch.object(attachment_memory, 'LIMIT', 1),
            self.assertRaises(ValueError),
        ):
            attachment_memory.cleanup(77, require_complete=True)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertTrue(first.text)
        self.assertEqual(second.version, 1)
        self.assertEqual(attachment_memory.residual(77), 2)
