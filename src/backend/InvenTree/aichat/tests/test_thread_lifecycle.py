"""Lifecycle pagination and deletion regressions; no model/provider calls."""

import tempfile
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from asgiref.sync import async_to_sync
from fastapi import Response

from ai.core import app as ai_app
from ai.core.auth import AIPrincipal
from aichat.models import AIRetentionOutbox, ChatMessage, ChatThread, MessageRole
from aichat.services import InvalidBoundary, ThreadNotFound, ThreadRepository, retention


@override_settings(FEATURE_THREAD_SHARING=True)
class ThreadLifecycleTests(TestCase):
    """Cursors never widen authorization; cleanup receipts survive parent deletion."""

    def setUp(self):
        """Create isolated actors and a disposable upload root."""
        self.owner = get_user_model().objects.create_user(username='lifecycle-owner')
        self.other = get_user_model().objects.create_user(username='lifecycle-other')
        from aichat.tests.memory_scope_fixtures import grant_shared_fixture_client

        grant_shared_fixture_client(self, self.owner, self.other)
        self.repo = ThreadRepository(self.owner.pk, 'site:main')
        self.other_repo = ThreadRepository(self.other.pk, 'site:main')
        self.thread, _ = self.repo.get_or_create(title='Pump history')
        self.uploads = tempfile.TemporaryDirectory()
        self.addCleanup(self.uploads.cleanup)
        patcher = mock.patch.object(
            retention, '_upload_root', return_value=Path(self.uploads.name)
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def add_messages(self, count):
        """Create an ordered transcript without invoking the model pipeline."""
        ChatMessage.objects.bulk_create([
            ChatMessage(
                thread=self.thread,
                sequence=index,
                role=MessageRole.USER,
                content=f'Turn {index}',
            )
            for index in range(1, count + 1)
        ])

    def test_cache_context_tracks_current_clients_without_disclosing_them(self):
        """Grant changes invalidate browser state; order does not change the tag."""
        with mock.patch(
            'tasks.scope.client_codes_for_actor', return_value={'alpha', 'beta'}
        ):
            first = self.repo.cache_context()
            self.assertEqual(first, self.repo.cache_context())
            self.assertNotEqual(first, self.other_repo.cache_context())
        with mock.patch('tasks.scope.client_codes_for_actor', return_value={'beta'}):
            self.assertNotEqual(first, self.repo.cache_context())
        self.assertRegex(first, r'^[a-f0-9]{64}$')
        self.assertNotIn('alpha', first)

    def test_message_pages_are_bounded_chronological_and_do_not_overlap(self):
        """Enforce the database limit and recover each sequence exactly once."""
        self.add_messages(123)
        with CaptureQueriesContext(connection) as queries:
            latest, more = self.repo.readable_message_page(self.thread.pk, limit=50)
        self.assertTrue(more)
        self.assertEqual([message.sequence for message in latest], list(range(74, 124)))
        message_queries = [
            query['sql']
            for query in queries
            if 'FROM "aichat_chatmessage"' in query['sql']
        ]
        self.assertEqual(len(message_queries), 1)
        self.assertIn('LIMIT 51', message_queries[0])
        middle, more = self.repo.readable_message_page(
            self.thread.pk, limit=50, before_sequence=74
        )
        self.assertTrue(more)
        first, more = self.repo.readable_message_page(
            self.thread.pk, limit=50, before_sequence=24
        )
        self.assertFalse(more)
        self.assertEqual(
            [message.sequence for message in first + middle + latest],
            list(range(1, 124)),
        )

    def test_next_page_reauthorizes_a_revoked_grant(self):
        """A cursor cannot preserve access after revocation."""
        self.add_messages(3)
        self.repo.share(self.thread.pk, grantee_id=self.other.pk)
        page, more = self.other_repo.readable_message_page(self.thread.pk, limit=1)
        self.assertTrue(more)
        self.repo.revoke_share(self.thread.pk, grantee_id=self.other.pk)
        with self.assertRaises(ThreadNotFound):
            self.other_repo.readable_message_page(
                self.thread.pk, before_sequence=page[0].sequence
            )

    def test_list_cursor_handles_timestamp_ties_and_binds_search_and_actor(self):
        """Stable ties page completely while changed boundaries reject cursors."""
        for index in range(4):
            self.repo.get_or_create(title=f'Pump {index}')
        ChatThread.objects.filter(owner=self.owner).update(updated_at=timezone.now())
        self.other_repo.get_or_create(title='Pump private')
        first, cursor = self.repo.list_page(limit=2, query='Pump')
        self.assertIsNotNone(cursor)
        with self.assertRaises(InvalidBoundary):
            self.other_repo.list_page(limit=2, cursor=cursor, query='Pump')
        with self.assertRaises(InvalidBoundary):
            self.repo.list_page(limit=2, cursor=cursor, query='Valve')
        with self.assertRaises(InvalidBoundary):
            ThreadRepository(self.owner.pk, 'site:other').list_page(
                limit=2, cursor=cursor, query='Pump'
            )
        with self.assertRaises(InvalidBoundary):
            self.repo.list_page(limit=2, cursor='corrupt', query='Pump')
        second, cursor = self.repo.list_page(limit=2, cursor=cursor, query='Pump')
        last, cursor = self.repo.list_page(limit=2, cursor=cursor, query='Pump')
        self.assertIsNone(cursor)
        ids = [thread.pk for thread in first + second + last]
        self.assertEqual(
            ids,
            sorted(
                ChatThread.objects.filter(owner=self.owner).values_list(
                    'pk', flat=True
                ),
                reverse=True,
            ),
        )
        self.assertEqual(len(set(ids)), 5)

    def test_cleanup_failure_is_retryable_only_by_the_original_boundary(self):
        """Parent deletion leaves a retryable receipt without widening access."""
        directory = Path(self.uploads.name) / self.thread.pk
        directory.mkdir()
        (directory / 'fixture.txt').write_text('Disposable test upload')
        with mock.patch.object(
            retention.shutil, 'rmtree', side_effect=OSError('fixture failure')
        ):
            first = self.repo.delete(self.thread.pk)
        self.assertEqual(first['status'], 'purge_incomplete')
        self.assertFalse(ChatThread.objects.filter(pk=self.thread.pk).exists())
        self.assertTrue(directory.exists())
        for repo in (self.other_repo, ThreadRepository(self.owner.pk, 'site:other')):
            with self.assertRaises(ThreadNotFound):
                repo.delete(self.thread.pk)
        self.assertEqual(self.repo.delete(self.thread.pk)['status'], 'deleted')
        self.assertFalse(directory.exists())
        self.assertEqual(self.repo.delete(self.thread.pk)['status'], 'deleted')
        with self.assertRaises(ThreadNotFound):
            self.repo.get_or_create(self.thread.pk)

    def test_unregistered_cleanup_cannot_be_reported_as_complete(self):
        """An unknown derivative kind keeps the purge incomplete."""
        AIRetentionOutbox.objects.create(
            kind='unknown_fixture',
            reference=self.thread.pk,
            next_attempt_at=timezone.now(),
        )
        self.assertEqual(self.repo.delete(self.thread.pk)['status'], 'purge_incomplete')

    def test_delete_route_projects_accepted_until_cleanup_finishes(self):
        """An incomplete external purge is HTTP 202 with a content-free status."""
        principal = AIPrincipal(
            subject=f'user:{self.owner.pk}',
            actor=f'user:{self.owner.pk}',
            user_pk=str(self.owner.pk),
            username=self.owner.username,
            authentication_method='session',
            scope='site:main',
            policy_version='test',
            is_staff=False,
            is_superuser=False,
        )
        directory = Path(self.uploads.name) / self.thread.pk
        directory.mkdir()
        response = Response()
        with (
            mock.patch.object(ai_app, '_principal', return_value=principal),
            mock.patch.object(
                retention.shutil, 'rmtree', side_effect=OSError('fixture failure')
            ),
        ):
            receipt = async_to_sync(ai_app.delete_thread)(self.thread.pk, response)
        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            receipt, {'status': 'purge_incomplete', 'thread_id': self.thread.pk}
        )

    def test_get_route_advertises_the_next_message_page(self):
        """The API publishes sequence cursors and an exhausted final page."""
        self.add_messages(3)
        principal = AIPrincipal(
            subject=f'user:{self.owner.pk}',
            actor=f'user:{self.owner.pk}',
            user_pk=str(self.owner.pk),
            username=self.owner.username,
            authentication_method='session',
            scope='site:main',
            policy_version='test',
            is_staff=False,
            is_superuser=False,
        )
        with mock.patch.object(ai_app, '_principal', return_value=principal):
            latest = async_to_sync(ai_app.get_thread)(self.thread.pk, message_limit=2)
            first = async_to_sync(ai_app.get_thread)(
                self.thread.pk,
                message_limit=2,
                before_sequence=latest['next_before_sequence'],
            )
        self.assertEqual(
            [message['sequence'] for message in latest['messages']], [2, 3]
        )
        self.assertTrue(latest['has_earlier'])
        self.assertEqual([message['sequence'] for message in first['messages']], [1])
        self.assertFalse(first['has_earlier'])
        self.assertIsNone(first['next_before_sequence'])
