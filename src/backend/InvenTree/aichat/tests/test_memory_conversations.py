"""Owner exclusion metadata and canonical mode-change API boundaries."""

import os
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase

from rest_framework.test import APIClient

from aichat.models import ChatThread
from aichat.services import memory_reads


class MemoryConversationTests(TestCase):
    """Private owner metadata must not expose another actor's conversation."""

    def setUp(self):
        """Prepare two accounts with disabled learning and no providers."""
        self.owner = get_user_model().objects.create_user(username='exclusions-owner')
        self.other = get_user_model().objects.create_user(username='exclusions-other')
        self.enterContext(mock.patch.dict(os.environ, {'INVENTREE_RESTORE_HOLD': '0'}))
        self.thread = ChatThread.objects.create(
            owner=self.owner,
            scope_key='site:main',
            scope_hash='fixture',
            namespace='unscoped',
        )
        self.foreign = ChatThread.objects.create(
            owner=self.other,
            scope_key='site:main',
            scope_hash='fixture',
            namespace='unscoped',
        )
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def test_list_contains_only_owned_content_free_metadata(self):
        """Titles, transcript and client identities are absent from settings pages."""
        result = memory_reads.excluded_conversations(self.owner)
        self.assertEqual(
            [row['thread_id'] for row in result['results']], [self.thread.pk]
        )
        self.assertEqual(
            set(result['results'][0]),
            {
                'thread_id',
                'created_at',
                'memory_mode',
                'effective_mode',
                'extraction_status',
                'recall_status',
            },
        )

    def test_foreign_mode_change_is_not_found(self):
        """Knowing a shared or guessed ID grants no owner setting authority."""
        response = self.client.put(
            f'/api/aichat/memory/conversations/{self.foreign.pk}/mode/',
            {'memory_mode': 'off'},
            format='json',
        )
        self.assertEqual(response.status_code, 404)
        self.foreign.refresh_from_db()
        self.assertEqual(self.foreign.memory_mode, 'inherit')

    def test_cursor_cannot_be_reused_by_another_owner(self):
        """Paging across exclusions retains account isolation."""
        ChatThread.objects.create(
            owner=self.owner,
            scope_key='site:main',
            scope_hash='fixture',
            namespace='unscoped',
        )
        with mock.patch.object(memory_reads, 'PAGE_SIZE', 1):
            cursor = memory_reads.excluded_conversations(self.owner)['next_cursor']
        with self.assertRaises(ValueError):
            memory_reads.excluded_conversations(self.other, cursor=cursor)
