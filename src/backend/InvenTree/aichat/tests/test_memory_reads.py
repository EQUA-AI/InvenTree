"""Owner inspection/export isolation and pagination qualification cases."""

import os
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from aichat.models import MemoryFact, UserMemorySettings
from aichat.services import memory_reads
from aichat.services.memory_policy import MemoryPolicyError


class MemoryReadTests(TestCase):
    """Inspection must not require a provider, enrollment or write permission."""

    def setUp(self):
        """Two actors with no client access; only their private preferences show."""
        self.owner = get_user_model().objects.create_user(username='memory-reader')
        self.other = get_user_model().objects.create_user(username='other-reader')
        self.enterContext(mock.patch.dict(os.environ, {'INVENTREE_RESTORE_HOLD': '0'}))
        self.enterContext(
            mock.patch.object(
                memory_reads, 'client_codes_for_actor', return_value=frozenset()
            )
        )
        self.fact = self.make_fact(self.owner, 'checklist')
        self.make_fact(self.other, 'checklist')

    def make_fact(self, owner, slot):
        """A proposed preference needs no fictional confirmation receipt."""
        return MemoryFact.objects.create(
            owner=owner,
            entity_kind='user',
            entity_id=str(owner.pk),
            slot_key=slot,
            memory_type='user_preference',
            text='I prefer a maintenance checklist.',
            text_lang='en',
            visibility_scope='user_private',
            classification='preference',
            origin='compaction',
            origin_modality='chat',
            notice_version='memory-v1',
            claim_fingerprint='a' * 64,
        )

    def test_inspection_never_lists_another_owner(self):
        """An actor without memory-write can still inspect their own content."""
        result = memory_reads.list_facts(self.owner, state='proposed')
        self.assertEqual([row['id'] for row in result['results']], [str(self.fact.pk)])
        self.assertNotIn('embedding', result['results'][0])
        self.assertFalse(result['results'][0]['source_available'])

    def test_opt_out_hides_content_before_cleanup_finishes(self):
        """Residual rows cannot leak while a bounded purge awaits retry."""
        UserMemorySettings.objects.create(user=self.owner, opted_out=True)
        self.assertEqual(
            memory_reads.list_facts(self.owner, state='proposed')['results'], []
        )

    def test_cursor_is_bound_to_owner_and_filters(self):
        """A valid signed cursor is still unusable by another owner or selection."""
        self.make_fact(self.owner, 'second')
        with mock.patch.object(memory_reads, 'PAGE_SIZE', 1):
            page = memory_reads.list_facts(self.owner, state='proposed')
        cursor = page['next_cursor']
        self.assertIsNotNone(cursor)
        with self.assertRaises(ValueError):
            memory_reads.list_facts(self.other, state='proposed', cursor=cursor)
        with self.assertRaises(ValueError):
            memory_reads.list_facts(self.owner, state='active', cursor=cursor)
        following = memory_reads.list_facts(self.owner, state='proposed', cursor=cursor)
        self.assertEqual(len(following['results']), 1)
        self.assertNotEqual(following['results'][0]['id'], page['results'][0]['id'])

    def test_stored_client_cannot_authorize_moved_entity(self):
        """Preview and list share a current entity check, not just a stored label."""
        self.fact.client_code = 'old-client'
        with mock.patch.object(
            memory_reads,
            '_client_for_entity',
            side_effect=MemoryPolicyError('source_scope'),
        ):
            self.assertFalse(
                memory_reads.can_read(self.owner, self.fact, clients={'old-client'})
            )
