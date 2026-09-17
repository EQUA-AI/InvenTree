"""Deferred regression cases for user cleanup and voice PROTECT ordering."""

import io
import json
import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.utils import timezone

from aichat.models import (
    AIRetentionOutbox,
    ChatThread,
    ChatThreadGrant,
    ChatThreadTombstone,
    MessageFeedback,
)
from aichat.services import ThreadRepository, retention
from aichat.services.voice_retention import purge_captures, purge_sessions
from voice.models import (
    VoiceCaptureReview,
    VoiceCaptureSession,
    VoiceOperation,
    VoiceSession,
    VoiceTranscriptAcceptance,
    VoiceTranscriptRevision,
)


@override_settings(FEATURE_THREAD_SHARING=True)
class UserErasureTests(TestCase):
    """Current-store erasure preserves other owners and protected evidence."""

    def setUp(self):
        """Create independent owners and isolate all upload cleanup."""
        users = get_user_model().objects
        self.owner = users.create_user(username='erasure-owner')
        self.other = users.create_user(username='erasure-other')
        self.repo = ThreadRepository(self.owner.pk, 'site:main')
        self.files = tempfile.TemporaryDirectory()
        self.addCleanup(self.files.cleanup)
        patcher = mock.patch.object(
            retention, '_upload_root', return_value=Path(self.files.name)
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def thread(self, owner=None, *, scope='site:main', name=None):
        """Create a transcript in a specified owner's boundary."""
        repository = ThreadRepository((owner or self.owner).pk, scope)
        thread, _ = repository.get_or_create(name, title='Private conversation')
        message = repository.append(
            thread.pk, role='assistant', content='Sensitive response'
        )
        return thread, message

    def session(self, *, owner=None, thread_id='thread_orphan'):
        """A terminal voice session may still be pinned by operational records."""
        return VoiceSession.objects.create(
            owner=owner or self.owner,
            thread_id=thread_id,
            scope_key='site:main',
            scope_hash='h' * 64,
            policy_version='test',
            state='ended',
            ended_at=timezone.now(),
        )

    def capture(self, *, owner=None, session=None, state='committed', key='capture'):
        """Build an accepted two-revision chain plus optional voice review."""
        owner = owner or self.owner
        capture = VoiceCaptureSession.objects.create(
            owner=owner,
            live_session=session,
            scope_key='site:main',
            scope_hash='h' * 64,
            purpose='closeout',
            target_work_order_id=41,
            target_version=1,
            state=state,
            consent_version='v1',
            consented_at=timezone.now(),
            policy_version='v1',
            idempotency_key=key,
        )
        first = VoiceTranscriptRevision.objects.create(
            capture=capture,
            revision=1,
            full_text='Private original',
            content_hash='a' * 64,
            created_by=owner,
        )
        final = VoiceTranscriptRevision.objects.create(
            capture=capture,
            revision=2,
            full_text='Private correction',
            content_hash='b' * 64,
            created_by=owner,
            supersedes=first,
        )
        VoiceTranscriptAcceptance.objects.create(
            revision=final, accepted_by=owner, content_hash=final.content_hash
        )
        capture.accepted_revision = final
        capture.save(update_fields=['accepted_revision'])
        if session:
            VoiceCaptureReview.objects.create(
                revision=final,
                session=session,
                scope_hash='h' * 64,
                target_version=1,
                policy_version='v1',
            )
        return capture

    def test_user_cleanup_spans_scopes_but_preserves_foreign_thread_and_audit(self):
        """Incoming grants are revoked; foreign transcript content survives."""
        main, _ = self.thread()
        alternate, _ = self.thread(scope='site:other')
        foreign, message = self.thread(self.other)
        ThreadRepository(self.other.pk, 'site:main').share(
            foreign.pk, grantee_id=self.owner.pk
        )
        feedback = MessageFeedback.objects.create(
            message=message, user=self.owner, rating='down', reason='Private feedback'
        )
        result = retention.purge_user(self.owner.pk, batch_size=1)
        self.assertEqual(result['status'], 'purged')
        self.assertFalse(result['account_erasure_complete'])
        self.assertEqual(result['backup_window']['status'], 'unverified')
        self.assertEqual(result['residuals']['owned_threads'], 0)
        self.assertFalse(
            ChatThread.objects.filter(pk__in=[main.pk, alternate.pk]).exists()
        )
        self.assertTrue(ChatThread.objects.filter(pk=foreign.pk).exists())
        self.assertFalse(MessageFeedback.objects.filter(pk=feedback.pk).exists())
        grant = ChatThreadGrant.objects.get(thread=foreign, grantee=self.owner)
        self.assertIsNotNone(grant.revoked_at)
        self.owner.refresh_from_db()
        self.assertTrue(self.owner.is_active)
        self.assertEqual(self.owner.username, 'erasure-owner')
        self.assertEqual(
            ChatThreadTombstone.objects.filter(
                owner=self.owner, reason='user_erasure'
            ).count(),
            2,
        )
        text = json.dumps(result)
        for private in ('erasure-owner', main.pk, foreign.pk, 'Private', 'Sensitive'):
            self.assertNotIn(private, text)
        retry = retention.purge_user(self.owner.pk)
        self.assertEqual(retry['status'], 'purged')
        self.assertEqual(retry['subject_hash'], result['subject_hash'])
        self.assertEqual(retry['processed']['grants_revoked'], 0)

    def test_settled_capture_review_is_removed_before_protected_session(self):
        """Account cleanup retries thread obligations after voice leaf cleanup."""
        thread, _ = self.thread()
        session = self.session(thread_id=thread.pk)
        capture = self.capture(session=session)
        foreign_session = self.session(owner=self.other, thread_id='thread_foreign')
        self.capture(owner=self.other, session=foreign_session, key='foreign')
        result = retention.purge_user(self.owner.pk, batch_size=1)
        self.assertEqual(result['status'], 'purged')
        self.assertFalse(VoiceCaptureSession.objects.filter(pk=capture.pk).exists())
        self.assertFalse(VoiceSession.objects.filter(pk=session.pk).exists())
        self.assertFalse(
            VoiceTranscriptRevision.objects.filter(capture_id=capture.pk).exists()
        )
        self.assertFalse(
            VoiceCaptureReview.objects.filter(session_id=session.pk).exists()
        )
        self.assertTrue(VoiceSession.objects.filter(pk=foreign_session.pk).exists())
        self.assertFalse(
            AIRetentionOutbox.objects
            .filter(reference=thread.pk)
            .exclude(state='done')
            .exists()
        )

    def test_active_capture_and_operation_pin_sessions_and_report_incomplete(self):
        """Erasure cannot force-delete active work or operational receipts."""
        active_session = self.session(thread_id='thread_active')
        capture = self.capture(session=active_session, state='active')
        pinned_session = self.session(thread_id='thread_receipt')
        operation = VoiceOperation.objects.create(
            session=pinned_session,
            decision_id='decision',
            source_id='source',
            action='closeout',
            state='completed',
            target_label='Private label',
            receipt={'receipt': 'Preserved operational evidence'},
        )
        result = retention.purge_user(self.owner.pk)
        self.assertEqual(result['status'], 'purge_incomplete')
        self.assertEqual(result['residuals']['unsettled_captures'], 1)
        self.assertEqual(result['processed']['voice_sessions_blocked'], 2)
        self.assertTrue(VoiceOperation.objects.filter(pk=operation.pk).exists())
        self.assertTrue(
            VoiceTranscriptAcceptance.objects.filter(revision__capture=capture).exists()
        )
        self.assertNotIn('Private label', json.dumps(result))

    def test_capture_protect_failure_rolls_back_review_acceptance_and_pin(self):
        """A malformed cross-capture reference cannot partially erase evidence."""
        session = self.session()
        capture = self.capture(session=session)
        foreign = self.capture(owner=self.other, key='foreign')
        foreign_revision = foreign.revisions.order_by('-revision').first()
        foreign_revision.supersedes = capture.accepted_revision
        foreign_revision.save(update_fields=['supersedes'])
        result = purge_captures(
            VoiceCaptureSession.objects.filter(pk=capture.pk), batch_size=1
        )
        self.assertEqual(result['voice_captures_blocked'], 1)
        capture.refresh_from_db()
        self.assertIsNotNone(capture.accepted_revision)
        self.assertEqual(capture.revisions.count(), 2)
        self.assertTrue(
            VoiceCaptureReview.objects.filter(revision__capture=capture).exists()
        )
        self.assertTrue(
            VoiceTranscriptAcceptance.objects.filter(revision__capture=capture).exists()
        )

    def test_protected_session_does_not_stop_other_session_cleanup(self):
        """One retained operational receipt leaves only its own session pending."""
        thread, _ = self.thread()
        pinned = self.session(thread_id=thread.pk)
        unpinned = self.session(thread_id=thread.pk)
        VoiceOperation.objects.create(
            session=pinned,
            decision_id='pinned',
            source_id='source',
            action='repair',
            target_label='private',
        )
        receipt = self.repo.delete(thread.pk)
        self.assertEqual(receipt['status'], 'purge_incomplete')
        self.assertFalse(VoiceSession.objects.filter(pk=unpinned.pk).exists())
        self.assertTrue(VoiceSession.objects.filter(pk=pinned.pk).exists())

    def test_expiry_orders_capture_before_linked_session_and_preserves_recent(self):
        """The scheduled family shares ordering but retains its age filters."""
        session = self.session()
        capture = self.capture(session=session)
        recent = self.capture(key='recent')
        old = timezone.now() - timedelta(days=401)
        VoiceSession.objects.filter(pk=session.pk).update(ended_at=old)
        VoiceCaptureSession.objects.filter(pk=capture.pk).update(updated_at=old)
        preview = retention.purge_expired_voice(dry_run=True)
        self.assertEqual(preview['voice_captures'], 1)
        self.assertTrue(VoiceCaptureSession.objects.filter(pk=capture.pk).exists())
        result = retention.purge_expired_voice(batch_size=1)
        self.assertEqual(result['status'], 'purged')
        self.assertEqual(result['voice_captures'], 1)
        self.assertEqual(result['voice_sessions'], 1)
        self.assertTrue(VoiceCaptureSession.objects.filter(pk=recent.pk).exists())

    def test_preview_and_registration_gap_make_no_changes(self):
        """Dry runs count only; incomplete derivative coverage blocks writes."""
        thread, _ = self.thread()
        capture = self.capture()
        preview = retention.purge_user(self.owner.pk, dry_run=True)
        self.assertEqual(preview['status'], 'dry_run')
        self.assertEqual(preview['before']['owned_threads'], 1)
        with mock.patch.object(
            retention.THREAD_DERIVATIVES,
            'uncovered_models',
            return_value=['future.model'],
        ):
            blocked = retention.purge_user(self.owner.pk)
        self.assertEqual(blocked['status'], 'purge_incomplete')
        self.assertTrue(ChatThread.objects.filter(pk=thread.pk).exists())
        self.assertTrue(VoiceCaptureSession.objects.filter(pk=capture.pk).exists())
        self.assertFalse(ChatThreadTombstone.objects.exists())

    def test_scheduled_receipt_keeps_blocked_voice_visible_as_an_error(self):
        """Counted blockers must not silently clear the operations error flag."""
        session = self.session()
        VoiceOperation.objects.create(
            session=session,
            decision_id='scheduled-pin',
            source_id='source',
            action='repair',
            target_label='private',
        )
        VoiceSession.objects.filter(pk=session.pk).update(
            ended_at=timezone.now() - timedelta(days=401)
        )
        with mock.patch.object(retention, '_write_last_run'):
            result = retention.run_all(families={'voice'})
        self.assertEqual(result['errors']['voice'], 'PurgeIncomplete')
        self.assertEqual(result['families']['voice']['voice_sessions_blocked'], 1)

    def test_retry_reprobes_completed_tombstones(self):
        """A failed residual is reported even when its previous outbox row is done."""
        thread, _ = self.thread()
        self.assertEqual(retention.purge_user(self.owner.pk)['status'], 'purged')
        directory = Path(self.files.name) / thread.pk
        directory.mkdir()
        (directory / 'artifact').write_text('private')
        with mock.patch.object(
            retention.shutil, 'rmtree', side_effect=OSError('secret')
        ):
            result = retention.purge_user(self.owner.pk)
        self.assertEqual(result['status'], 'purge_incomplete')
        self.assertNotIn('secret', json.dumps(result))
        self.assertEqual(retention.purge_user(self.owner.pk)['status'], 'purged')
        self.assertFalse(directory.exists())

    def test_command_defaults_to_preview_and_exits_nonzero_for_residuals(self):
        """Execution is explicit and incomplete cleanup cannot exit successfully."""
        thread, _ = self.thread()
        output = io.StringIO()
        call_command('ai_purge_user', user_id=self.owner.pk, stdout=output)
        self.assertEqual(json.loads(output.getvalue())['status'], 'dry_run')
        self.assertTrue(ChatThread.objects.filter(pk=thread.pk).exists())
        self.capture(state='review')
        output = io.StringIO()
        with self.assertRaises(CommandError):
            call_command(
                'ai_purge_user', user_id=self.owner.pk, execute=True, stdout=output
            )
        self.assertEqual(json.loads(output.getvalue())['status'], 'purge_incomplete')

    def test_invalid_subject_or_batch_never_mutates(self):
        """Reject ambiguous scalars and unbounded operator requests."""
        thread, _ = self.thread()
        for user_id, batch_size in (
            (True, 10),
            (-1, 10),
            (self.owner.pk, 0),
            (self.owner.pk, 1001),
        ):
            with self.subTest(user_id=user_id, batch_size=batch_size):
                with self.assertRaises(ValueError):
                    retention.purge_user(user_id, batch_size=batch_size)
        self.assertTrue(ChatThread.objects.filter(pk=thread.pk).exists())

    def test_concurrent_new_thread_prevents_a_clean_user_receipt(self):
        """Without an account write hold, later writes require another pass."""
        self.thread()

        def write_during_cleanup(queryset, *, batch_size):
            self.thread(name='thread_created_during_erasure')
            return purge_sessions(queryset, batch_size=batch_size)

        with mock.patch(
            'aichat.services.user_erasure.purge_sessions',
            side_effect=write_during_cleanup,
        ):
            result = retention.purge_user(self.owner.pk)
        self.assertEqual(result['status'], 'purge_incomplete')
        self.assertEqual(result['residuals']['owned_threads'], 1)
        self.assertEqual(retention.purge_user(self.owner.pk)['status'], 'purged')
