"""Deferred restore-journal and serving-hold regression cases."""

import io
import json
import os
import stat
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import signing
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from asgiref.sync import async_to_sync

from aichat.models import AIRetentionOutbox, ChatThread, ChatThreadTombstone
from aichat.services import ThreadRepository, retention
from aichat.services.retention_journal import (
    SALT,
    JournalError,
    export_journal,
    read_journal,
    replay_journal,
)
from InvenTree.restore_hold import (
    RestoreHoldASGI,
    RestoreHoldWSGI,
    restore_hold_enabled,
)


class RetentionJournalTests(TestCase):
    """A source deletion can be replayed without trusting rolled-back tombstones."""

    def setUp(self):
        """Isolate source identity, serving hold and upload storage."""
        self.user = get_user_model().objects.create_user(username='journal-owner')
        self.repo = ThreadRepository(self.user.pk, 'site:main')
        self.files = tempfile.TemporaryDirectory()
        self.addCleanup(self.files.cleanup)
        for patcher in (
            mock.patch.dict(
                os.environ,
                {
                    'INVENTREE_RETENTION_JOURNAL_SOURCE': 'journal-fixture',
                    'INVENTREE_RESTORE_HOLD': '1',
                },
            ),
            mock.patch.object(
                retention,
                '_upload_root',
                return_value=Path(self.files.name) / 'uploads',
            ),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.since = (timezone.now() - timedelta(hours=1)).isoformat()

    def source_deletion(self):
        """Capture enough root metadata to emulate an older restored snapshot."""
        thread, _ = self.repo.get_or_create(title='Private title')
        self.repo.append(thread.pk, role='user', content='Private transcript')
        snapshot = {
            'id': thread.pk,
            'owner_id': self.user.pk,
            'scope_key': thread.scope_key,
            'scope_hash': thread.scope_hash,
            'namespace': thread.namespace,
            'created_at': thread.created_at,
        }
        self.repo.delete(thread.pk)
        return snapshot

    def restore(self, snapshot):
        """Restore the old root, with its tombstones/outbox rolled back too."""
        ChatThreadTombstone.objects.filter(thread_id=snapshot['id']).delete()
        AIRetentionOutbox.objects.filter(reference=snapshot['id']).delete()
        thread = ChatThread.objects.create(**snapshot, title='Restored private title')
        ChatThread.objects.filter(pk=thread.pk).update(
            created_at=snapshot['created_at']
        )
        self.repo.append(thread.pk, role='user', content='Restored private transcript')
        return thread

    def test_roundtrip_replay_restores_tombstone_and_preserves_original_clock(self):
        """A restored root is erased and its original journal timestamp survives."""
        snapshot = self.source_deletion()
        source_time = ChatThreadTombstone.objects.get(
            thread_id=snapshot['id']
        ).deleted_at
        token = export_journal(since=self.since)
        payload = read_journal(token, since=self.since)
        self.assertNotIn('Private', json.dumps(payload))
        self.restore(snapshot)
        preview = replay_journal(token, since=self.since)
        self.assertEqual(preview['status'], 'dry_run')
        self.assertTrue(ChatThread.objects.filter(pk=snapshot['id']).exists())
        result = replay_journal(token, since=self.since, execute=True)
        self.assertEqual(result['status'], 'replayed')
        self.assertFalse(result['hold_released'])
        self.assertFalse(ChatThread.objects.filter(pk=snapshot['id']).exists())
        self.assertEqual(
            ChatThreadTombstone.objects.get(thread_id=snapshot['id']).deleted_at,
            source_time,
        )
        self.assertNotIn(snapshot['id'], json.dumps(result))
        self.assertEqual(
            replay_journal(token, since=self.since, execute=True)['status'], 'replayed'
        )

    def test_missing_parent_still_gets_receipt_and_derivative_cleanup(self):
        """Orphan uploads are removed even when the restored root never existed."""
        snapshot = self.source_deletion()
        token = export_journal(since=self.since)
        ChatThreadTombstone.objects.filter(thread_id=snapshot['id']).delete()
        AIRetentionOutbox.objects.all().delete()
        directory = Path(self.files.name) / 'uploads' / snapshot['id']
        directory.mkdir(parents=True)
        (directory / 'upload').write_text('private')
        result = replay_journal(token, since=self.since, execute=True)
        self.assertEqual(result['status'], 'replayed')
        self.assertFalse(directory.exists())
        self.assertTrue(
            ChatThreadTombstone.objects.filter(thread_id=snapshot['id']).exists()
        )

    def test_tampering_wrong_source_and_wrong_restore_point_refuse_without_writes(self):
        """Signatures and the complete environment/window contract are checked."""
        snapshot = self.source_deletion()
        token = export_journal(since=self.since)
        self.restore(snapshot)
        with self.assertRaises(JournalError):
            replay_journal(token + 'x', since=self.since, execute=True)
        with self.assertRaises(JournalError):
            replay_journal(token, since=timezone.now().isoformat(), execute=True)
        with mock.patch.dict(
            os.environ, {'INVENTREE_RETENTION_JOURNAL_SOURCE': 'other'}
        ):
            with self.assertRaises(JournalError):
                replay_journal(token, since=self.since, execute=True)
        self.assertTrue(ChatThread.objects.filter(pk=snapshot['id']).exists())
        self.assertFalse(ChatThreadTombstone.objects.exists())

    def test_conflict_preflight_refuses_entire_journal(self):
        """A reused id cannot erase another thread, or partially apply the file."""
        first, second = self.source_deletion(), self.source_deletion()
        token = export_journal(since=self.since)
        self.restore(first)
        second_root = self.restore(second)
        ChatThread.objects.filter(pk=second_root.pk).update(scope_hash='f' * 64)
        result = replay_journal(token, since=self.since, execute=True)
        self.assertEqual(result['status'], 'replay_incomplete')
        self.assertEqual(result['conflicts'], 1)
        self.assertEqual(ChatThread.objects.count(), 2)
        self.assertFalse(ChatThreadTombstone.objects.exists())

    def test_execute_requires_hold_and_never_clears_it(self):
        """The command's execution path cannot bypass the serving hold guard."""
        snapshot = self.source_deletion()
        token = export_journal(since=self.since)
        self.restore(snapshot)
        with mock.patch.dict(os.environ, {'INVENTREE_RESTORE_HOLD': '0'}):
            with self.assertRaises(JournalError):
                replay_journal(token, since=self.since, execute=True)
        self.assertTrue(ChatThread.objects.filter(pk=snapshot['id']).exists())
        self.assertTrue(restore_hold_enabled())

    def test_live_summary_obligation_is_not_authority_to_erase_a_thread(self):
        """A lost exclusion payload must remain an explicit restore blocker."""
        thread, _ = self.repo.get_or_create()
        retention.enqueue_outbox('thread_summary', thread.pk)
        token = export_journal(since=self.since)
        result = replay_journal(token, since=self.since, execute=True)
        self.assertEqual(result['status'], 'replay_incomplete')
        self.assertEqual(result['unsupported_obligations'], 1)
        self.assertTrue(ChatThread.objects.filter(pk=thread.pk).exists())

    def test_pending_orphan_upload_is_replayed_without_erasing_roots(self):
        """Outstanding orphan work retains its independent upload cleanup path."""
        retention.enqueue_outbox('upload_dir', 'thread_orphan')
        directory = Path(self.files.name) / 'uploads' / 'thread_orphan'
        directory.mkdir(parents=True)
        (directory / 'upload').write_text('private')
        token = export_journal(since=self.since)
        with mock.patch.object(
            retention.shutil, 'rmtree', side_effect=OSError('secret')
        ):
            failed = replay_journal(token, since=self.since, execute=True)
        self.assertEqual(failed['status'], 'replay_incomplete')
        self.assertNotIn('secret', json.dumps(failed))
        self.assertTrue(AIRetentionOutbox.objects.exclude(state='done').exists())
        self.assertEqual(
            replay_journal(token, since=self.since, execute=True)['status'], 'replayed'
        )
        self.assertFalse(directory.exists())

    def test_unknown_manifest_kind_and_incomplete_schema_refuse(self):
        """Even correctly signed input needs a supported complete contract."""
        snapshot = self.source_deletion()
        token = export_journal(since=self.since)
        payload = read_journal(token, since=self.since)
        self.restore(snapshot)
        payload['kinds'].append('future_kind')
        future = signing.dumps(payload, salt=SALT)
        self.assertEqual(
            replay_journal(future, since=self.since, execute=True)['unknown_kinds'], 1
        )
        payload['threads'][0]['unexpected'] = 'private'
        with self.assertRaises(JournalError):
            replay_journal(
                signing.dumps(payload, salt=SALT), since=self.since, execute=True
            )
        self.assertTrue(ChatThread.objects.filter(pk=snapshot['id']).exists())

    def test_export_limits_refuse_truncation(self):
        """Too many records never produce a deceptively complete journal."""
        self.source_deletion()
        self.source_deletion()
        with mock.patch('aichat.services.retention_journal.MAX_ROWS', 1):
            with self.assertRaises(JournalError):
                export_journal(since=self.since)

    def test_older_tombstone_is_included_when_cleanup_is_still_owed(self):
        """The restore timestamp must not drop an older outstanding obligation."""
        thread, _ = self.repo.get_or_create()
        ChatThread.objects.filter(pk=thread.pk).update(
            created_at=timezone.now() - timedelta(days=3)
        )
        self.repo.delete(thread.pk)
        ChatThreadTombstone.objects.filter(thread_id=thread.pk).update(
            deleted_at=timezone.now() - timedelta(days=2)
        )
        retention.enqueue_outbox('upload_dir', thread.pk)
        payload = read_journal(export_journal(since=self.since), since=self.since)
        self.assertEqual([row['thread_id'] for row in payload['threads']], [thread.pk])

    def test_thread_cleanup_failure_retains_hold_and_can_be_retried(self):
        """Parent removal alone cannot qualify replay while uploads remain."""
        snapshot = self.source_deletion()
        token = export_journal(since=self.since)
        self.restore(snapshot)
        directory = Path(self.files.name) / 'uploads' / snapshot['id']
        directory.mkdir(parents=True)
        (directory / 'upload').write_text('private')
        with mock.patch.object(
            retention.shutil, 'rmtree', side_effect=OSError('secret')
        ):
            result = replay_journal(token, since=self.since, execute=True)
        self.assertEqual(result['status'], 'replay_incomplete')
        self.assertFalse(result['hold_released'])
        self.assertTrue(AIRetentionOutbox.objects.exclude(state='done').exists())
        self.assertEqual(
            replay_journal(token, since=self.since, execute=True)['status'], 'replayed'
        )
        self.assertFalse(directory.exists())

    def test_commands_write_private_artifact_and_preview_without_mutation(self):
        """Command output is aggregate-only and existing journals survive."""
        snapshot = self.source_deletion()
        target = Path(self.files.name) / 'journal.signed'
        output = io.StringIO()
        call_command(
            'retention_journal_export',
            since=self.since,
            output=str(target),
            stdout=output,
        )
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)
        self.assertNotIn(snapshot['id'], output.getvalue())
        original = target.read_bytes()
        with self.assertRaises(CommandError):
            call_command(
                'retention_journal_export', since=self.since, output=str(target)
            )
        self.assertEqual(target.read_bytes(), original)
        self.restore(snapshot)
        output = io.StringIO()
        call_command(
            'retention_replay', since=self.since, journal=str(target), stdout=output
        )
        self.assertEqual(json.loads(output.getvalue())['status'], 'dry_run')
        self.assertTrue(ChatThread.objects.filter(pk=snapshot['id']).exists())


class RestoreHoldTests(SimpleTestCase):
    """All HTTP mounts and new sockets are blocked before application dispatch."""

    def test_asgi_hold_blocks_http_and_websocket_without_dispatch(self):
        """No held route, including health and the AI mount, reaches its handler."""
        for scope in (
            {'type': 'http', 'path': '/health/live'},
            {'type': 'http', 'path': '/api/ai/threads'},
            {'type': 'websocket', 'path': '/api/ai/voice'},
        ):
            with (
                self.subTest(scope=scope),
                mock.patch.dict(os.environ, {'INVENTREE_RESTORE_HOLD': '1'}),
            ):
                application, receive, send = (
                    mock.AsyncMock(),
                    mock.AsyncMock(),
                    mock.AsyncMock(),
                )
                async_to_sync(RestoreHoldASGI(application))(scope, receive, send)
                application.assert_not_awaited()
                if scope['type'] == 'http':
                    self.assertEqual(send.await_args_list[0].args[0]['status'], 503)
                else:
                    self.assertEqual(send.await_args.args[0]['code'], 1013)

    def test_wsgi_hold_and_normal_passthrough(self):
        """A misspelled nonempty hold value fails closed; explicit off passes."""
        application, start_response = mock.Mock(return_value=[b'normal']), mock.Mock()
        middleware = RestoreHoldWSGI(application)
        with mock.patch.dict(os.environ, {'INVENTREE_RESTORE_HOLD': 'tru'}):
            self.assertEqual(middleware({}, start_response), [b'Restore in progress.'])
            application.assert_not_called()
            self.assertEqual(
                start_response.call_args.args[0], '503 Service Unavailable'
            )
        with mock.patch.dict(os.environ, {'INVENTREE_RESTORE_HOLD': 'false'}):
            self.assertEqual(middleware({}, start_response), [b'normal'])

    def test_asgi_unheld_and_lifespan_scopes_pass_through(self):
        """The wrapper preserves ordinary dispatch and ASGI lifespan handling."""
        application, receive, send = (
            mock.AsyncMock(),
            mock.AsyncMock(),
            mock.AsyncMock(),
        )
        with mock.patch.dict(os.environ, {'INVENTREE_RESTORE_HOLD': '0'}):
            async_to_sync(RestoreHoldASGI(application))({'type': 'http'}, receive, send)
        with mock.patch.dict(os.environ, {'INVENTREE_RESTORE_HOLD': '1'}):
            async_to_sync(RestoreHoldASGI(application))(
                {'type': 'lifespan'}, receive, send
            )
        self.assertEqual(application.await_count, 2)
