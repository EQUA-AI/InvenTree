"""Scoped bulk deletion and export regression cases, deferred for qualification."""

import io
import json
import stat
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from asgiref.sync import async_to_sync
from fastapi import HTTPException, Response

from ai.core import app as ai_app
from ai.core.auth import AIPrincipal
from aichat.models import AIRetentionOutbox, ChatThread, ChatThreadGrant
from aichat.services import InvalidBoundary, ThreadRepository, retention


@override_settings(FEATURE_THREAD_SHARING=True)
class OwnedLifecycleTests(TestCase):
    """Only owned server-scope conversations enter the lifecycle scan."""

    def setUp(self):
        """Create independent boundaries and isolate filesystem cleanup."""
        users = get_user_model().objects
        self.owner = users.create_user(username='bulk-owner')
        self.other = users.create_user(username='bulk-other')
        self.repo = ThreadRepository(self.owner.pk, 'site:main')
        self.other_repo = ThreadRepository(self.other.pk, 'site:main')
        self.second_scope = ThreadRepository(self.owner.pk, 'site:other')
        self.files = tempfile.TemporaryDirectory()
        self.addCleanup(self.files.cleanup)
        patcher = mock.patch.object(
            retention, '_upload_root', return_value=Path(self.files.name) / 'uploads'
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def thread(self, repository=None, *, name='thread_a', text='Owner message'):
        """Create a transcript with stable ordering and export provenance."""
        repository = repository or self.repo
        thread, _ = repository.get_or_create(name, title='Owner title')
        repository.append(thread.pk, role='user', content=text)
        return thread

    def test_prepared_deletion_is_read_only_and_keeps_its_cutoff_on_retry(self):
        """Even the first lost deletion response cannot widen a prepared scan."""
        original = self.thread()
        plan = self.repo.prepare_deletion()
        self.assertTrue(ChatThread.objects.filter(pk=original.pk).exists())
        self.assertFalse(AIRetentionOutbox.objects.exists())
        later = self.thread(name='thread_later')
        ChatThread.objects.filter(pk=later.pk).update(
            created_at=timezone.now() + timedelta(seconds=1)
        )
        for _attempt in range(2):
            receipt = self.repo.delete_all(request_token=plan['request_token'])
            self.assertEqual(receipt['status'], 'deleted')
            self.assertEqual(receipt['cutoff'], plan['cutoff'])
            self.assertEqual(receipt['request_token'], plan['request_token'])
            self.assertTrue(ChatThread.objects.filter(pk=later.pk).exists())
        self.assertFalse(ChatThread.objects.filter(pk=original.pk).exists())
        with self.assertRaises(InvalidBoundary):
            self.second_scope.delete_all(request_token=plan['request_token'])

    def test_delete_all_keeps_foreign_shared_and_other_scope_threads(self):
        """Sharing never expands deletion authority or reveals foreign ids."""
        own = self.thread()
        foreign = self.thread(self.other_repo, name='thread_foreign')
        scoped = self.thread(self.second_scope, name='thread_scope')
        self.other_repo.share(foreign.pk, grantee_id=self.owner.pk)
        result = self.repo.delete_all()
        self.assertEqual(result['status'], 'deleted')
        self.assertEqual([row['thread_id'] for row in result['results']], [own.pk])
        self.assertTrue(ChatThread.objects.filter(pk=foreign.pk).exists())
        self.assertTrue(ChatThread.objects.filter(pk=scoped.pk).exists())
        self.assertTrue(ChatThreadGrant.objects.filter(thread=foreign).exists())

    def test_bounded_pages_and_restart_keep_the_original_cutoff(self):
        """Retrying scans tombstones and never deletes later conversations."""
        self.thread(name='thread_a')
        self.thread(name='thread_b')
        first = self.repo.delete_all(limit=1)
        self.assertEqual(first['status'], 'purge_incomplete')
        self.assertEqual(len(first['results']), 1)
        self.assertIsNotNone(first['next_cursor'])
        later = self.thread(name='thread_c')
        ChatThread.objects.filter(pk=later.pk).update(
            created_at=timezone.now() + timedelta(seconds=1)
        )
        final = self.repo.delete_all(
            limit=1, request_token=first['request_token'], cursor=first['next_cursor']
        )
        self.assertEqual(final['status'], 'deleted')
        self.assertEqual(final['processed'], 2)
        replay = self.repo.delete_all(request_token=first['request_token'])
        self.assertEqual(replay['status'], 'deleted')
        self.assertEqual(replay['processed'], 2)
        self.assertTrue(ChatThread.objects.filter(pk=later.pk).exists())

    def test_failure_cannot_be_hidden_by_a_later_clean_page(self):
        """A restarted scan repairs failures; continuation retains their count."""
        self.thread(name='thread_a')
        self.thread(name='thread_b')
        with mock.patch.object(self.repo, 'delete', side_effect=RuntimeError('secret')):
            first = self.repo.delete_all(limit=1)
        self.assertEqual(first['incomplete'], 1)
        self.assertNotIn('secret', json.dumps(first))
        final = self.repo.delete_all(
            limit=1, request_token=first['request_token'], cursor=first['next_cursor']
        )
        self.assertIsNone(final['next_cursor'])
        self.assertEqual(final['status'], 'purge_incomplete')
        self.assertTrue(ChatThread.objects.filter(pk='thread_a').exists())
        restarted = self.repo.delete_all(request_token=first['request_token'])
        self.assertEqual(restarted['status'], 'deleted')
        self.assertEqual(restarted['incomplete'], 0)

    def test_tombstone_retries_reprobe_even_completed_obligations(self):
        """A residual returning after deletion is repaired by the next scan."""
        thread = self.thread()
        first = self.repo.delete_all()
        self.assertEqual(first['status'], 'deleted')
        directory = Path(self.files.name) / 'uploads' / thread.pk
        directory.mkdir(parents=True)
        (directory / 'residual.txt').write_text('fixture')
        with mock.patch.object(
            retention.shutil, 'rmtree', side_effect=OSError('secret')
        ):
            failed = self.repo.delete_all(request_token=first['request_token'])
        self.assertEqual(failed['status'], 'purge_incomplete')
        self.assertTrue(
            AIRetentionOutbox.objects
            .filter(reference=thread.pk)
            .exclude(state='done')
            .exists()
        )
        fixed = self.repo.delete_all(request_token=first['request_token'])
        self.assertEqual(fixed['status'], 'deleted')
        self.assertFalse(directory.exists())

    def test_late_visible_root_behind_cursor_prevents_completion(self):
        """A transaction visible after page one cannot silently escape deletion."""
        self.thread(name='thread_b')
        self.thread(name='thread_c')
        first = self.repo.delete_all(limit=1)
        late = self.thread(name='thread_a')
        # Simulate a create begun before the cutoff becoming visible later.
        ChatThread.objects.filter(pk=late.pk).update(
            created_at=timezone.now() - timedelta(minutes=1)
        )
        final = self.repo.delete_all(
            limit=1, request_token=first['request_token'], cursor=first['next_cursor']
        )
        self.assertIsNone(final['next_cursor'])
        self.assertEqual(final['status'], 'purge_incomplete')
        self.assertEqual(final['remaining_threads'], 1)
        self.assertEqual(
            self.repo.delete_all(request_token=first['request_token'])['status'],
            'deleted',
        )

    def test_empty_scan_cannot_qualify_missing_derivative_coverage(self):
        """Missing registration is incomplete even when no roots are visible."""
        with mock.patch.object(
            retention.THREAD_DERIVATIVES, 'uncovered_models', return_value=['new.model']
        ):
            self.assertEqual(self.repo.delete_all()['status'], 'purge_incomplete')

    def test_tokens_cannot_cross_boundaries_or_be_tampered_with(self):
        """Signed positions are bound to owner, scope, purpose and cutoff."""
        self.thread(name='thread_a')
        self.thread(name='thread_b')
        first = self.repo.delete_all(limit=1)
        for repository in (self.other_repo, self.second_scope):
            with self.subTest(actor=repository.actor_id, scope=repository.scope_key):
                with self.assertRaises(InvalidBoundary):
                    repository.delete_all(request_token=first['request_token'])
        for arguments in (
            {'request_token': first['request_token'] + 'x'},
            {'request_token': first['next_cursor']},
            {'cursor': first['next_cursor']},
            {'limit': 101},
            {'limit': True},
        ):
            with self.subTest(arguments=arguments):
                with self.assertRaises(InvalidBoundary):
                    self.repo.delete_all(**arguments)
        with mock.patch(
            'django.core.signing.time.time',
            return_value=timezone.now().timestamp() + 90000,
        ):
            with self.assertRaises(InvalidBoundary):
                self.repo.delete_all(request_token=first['request_token'])

        second = self.repo.delete_all(limit=1)
        with self.assertRaises(InvalidBoundary):
            self.repo.delete_all(
                request_token=second['request_token'], cursor=first['next_cursor']
            )

    def test_export_is_bounded_and_projects_only_owned_transcripts(self):
        """Multiple ORM pages preserve text/provenance and omit raw metadata."""
        thread = self.thread()
        self.repo.append(
            thread.pk,
            role='assistant',
            content='Owner reply',
            metadata={'secret': 'omit'},
        )
        foreign = self.thread(self.other_repo, name='thread_foreign', text='foreign')
        self.other_repo.share(foreign.pk, grantee_id=self.owner.pk)
        self.thread(self.second_scope, name='thread_scope', text='other scope')
        with CaptureQueriesContext(connection) as queries:
            records = list(self.repo.export_transcript_records(chunk_size=1))
        messages = [row for row in records if row['type'] == 'message']
        self.assertEqual([row['sequence'] for row in messages], [1, 2])
        self.assertEqual(
            [row['content'] for row in messages], ['Owner message', 'Owner reply']
        )
        self.assertEqual({row['thread_id'] for row in messages}, {thread.pk})
        self.assertNotIn('secret', json.dumps(records))
        self.assertNotIn('foreign', json.dumps(records))
        self.assertEqual(records[0]['scope'], 'owned_thread_transcripts')
        self.assertEqual(records[-1]['messages'], 2)
        self.assertEqual(records[-1]['threads'], 1)
        page_queries = [
            row['sql']
            for row in queries
            if 'FROM "aichat_chatmessage"' in row['sql'] and 'MAX(' not in row['sql']
        ]
        self.assertTrue(page_queries)
        self.assertTrue(all('LIMIT 1' in sql for sql in page_queries))

    def test_export_excludes_later_messages_and_reauthorizes_pages(self):
        """A streamed export never widens to writes after its start time."""
        thread = self.thread()
        records = self.repo.export_transcript_records(chunk_size=1)
        self.assertEqual(next(records)['type'], 'manifest')
        self.repo.append(thread.pk, role='user', content='Later message')
        tail = list(records)
        self.assertEqual(
            [r['content'] for r in tail if r['type'] == 'message'], ['Owner message']
        )
        records = self.repo.export_transcript_records(chunk_size=1)
        next(records)
        next(records)
        # Reassigning is not a supported product action; this fixture proves
        # subsequent pages still apply the live boundary rather than cached ids.
        ChatThread.objects.filter(pk=thread.pk).update(owner=self.other)
        self.assertFalse(any(r['type'] == 'message' for r in records))

    def test_export_command_uses_private_file_and_never_overwrites(self):
        """Sensitive content goes to a new file, never command output."""
        self.thread()
        target = Path(self.files.name) / 'export.json'
        output = io.StringIO()
        call_command(
            'ai_export_user',
            user_id=self.owner.pk,
            scope_key='site:main',
            output=str(target),
            stdout=output,
        )
        payload = json.loads(target.read_text())
        self.assertEqual(payload['records'][-1]['type'], 'complete')
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        self.assertNotIn('Owner message', output.getvalue())
        original = target.read_bytes()
        with self.assertRaises(CommandError):
            call_command(
                'ai_export_user',
                user_id=self.owner.pk,
                scope_key='site:main',
                output=str(target),
            )
        self.assertEqual(target.read_bytes(), original)

    def test_failed_export_removes_partial_file_without_error_content(self):
        """The operator receives no database/provider exception text."""
        target = Path(self.files.name) / 'export.json'
        with mock.patch.object(
            ThreadRepository,
            'export_transcript_records',
            side_effect=RuntimeError('secret'),
        ):
            with self.assertRaises(CommandError) as caught:
                call_command(
                    'ai_export_user',
                    user_id=self.owner.pk,
                    scope_key='site:main',
                    output=str(target),
                )
        self.assertFalse(target.exists())
        self.assertNotIn('secret', str(caught.exception))

    def test_api_projects_pending_status_and_rejects_foreign_token(self):
        """The route resolves the owner from the authenticated principal."""
        self.thread(name='thread_a')
        self.thread(name='thread_b')
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
        response = Response()
        with mock.patch.object(ai_app, '_principal', return_value=principal):
            preparation = Response()
            plan = async_to_sync(ai_app.prepare_thread_deletion)(preparation)
            self.assertEqual(preparation.headers['Cache-Control'], 'private, no-store')
            self.assertEqual(ChatThread.objects.count(), 2)
            result = async_to_sync(ai_app.delete_all_threads)(
                ai_app.ThreadDeleteAllRequest(
                    confirm=True, limit=1, request_token=plan['request_token']
                ),
                response,
            )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.headers['Cache-Control'], 'private, no-store')
        foreign = self.other_repo.delete_all()
        with mock.patch.object(ai_app, '_principal', return_value=principal):
            with self.assertRaises(HTTPException) as caught:
                async_to_sync(ai_app.delete_all_threads)(
                    ai_app.ThreadDeleteAllRequest(
                        confirm=True, request_token=foreign['request_token']
                    ),
                    Response(),
                )
        self.assertEqual(caught.exception.status_code, 400)
        self.assertEqual(len(result['results']), 1)
