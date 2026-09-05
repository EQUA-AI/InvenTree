"""S16 retention reconciliation (Q48/§8.11) — the gate-11 test evidence.

400-day purge, immediate content deletion, protected grants/tombstones,
24-hour uploads, outbox retries, aggregate-before-scrub, idempotent
reruns. Time is frozen by post-hoc queryset ``update()`` on ``auto_now``
fields — the purge selects strictly by stored timestamps.
"""

import os
import tempfile
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models.deletion import ProtectedError
from django.test import TestCase, override_settings
from django.utils import timezone

from aichat.models import (
    AIQuotaAuditAction,
    AIQuotaAuditEvent,
    AIQuotaReservation,
    AIQuotaReservationState,
    AIRequestRejection,
    AIRetentionOutbox,
    AIUsageMonthlyAggregate,
    ChatActionProposal,
    ChatEvidenceSet,
    ChatEvidenceSetMember,
    ChatMessage,
    ChatThread,
    ChatThreadGrant,
    ChatThreadGrantTombstone,
    ChatThreadTombstone,
    ChatTurn,
    MessageFeedback,
    ProposalAction,
    ProposalState,
    RetrievalMiss,
    TurnModality,
    TurnState,
)
from aichat.services import ThreadRepository, canonical_request_fingerprint, retention

OLD_DAYS = 401
DETAIL_OLD_DAYS = 91


def _old(days=OLD_DAYS):
    return timezone.now() - timedelta(days=days)


def _canonical_result() -> dict:
    return {
        'kind': 'evidence_analysis',
        'response_version': 2,
        'response_state': 'complete',
        'detailed_response': '2 matching records were found. [1]',
        'spoken_summary': '',
    }


class RetentionEnvMixin:
    """Builds full thread graphs through the real repository."""

    _seq = 0

    def make_user(self, username):
        """Create one plain user."""
        return get_user_model().objects.create_user(username=username)

    def _set_spec(self) -> dict:
        RetentionEnvMixin._seq += 1
        return {
            'id': f'set_{RetentionEnvMixin._seq:032d}',
            'source_class': 'work_order',
            'filters': {'machine_ids': [12]},
            'population_count': 2,
            'evaluated_count': 2,
            'displayed_count': 2,
            'complete_population': True,
            'high_watermarks': {'updated_at': '2026-08-27T00:00:00+00:00'},
            'snapshot_hash': 'snap_test',
            'supports_expansion': True,
            'member_cap': 25000,
            'calculation': {'operation': 'count', 'result': '2'},
            'members': [(1, 'work_order', '41', 'v3'), (2, 'work_order', '42', '')],
            'authorization_scope_hash': 'authhash',
            'analysis_scope_hash': 'scopehash',
        }

    def build_thread(self, username, *, grants=(), voice=False, proposals=False):
        """One complete thread: turn, messages, evidence set, feedback."""
        user = self.make_user(username)
        repository = ThreadRepository(user.pk, 'site:main')
        thread, _ = repository.get_or_create()
        fingerprint = canonical_request_fingerprint(
            content='How many open work orders?',
            modality=TurnModality.TEXT,
            trusted_context={},
        )
        result = repository.begin_turn(
            thread.pk,
            content='How many open work orders?',
            modality=TurnModality.TEXT,
            trusted_context={},
            modality_metadata={},
            idempotency_key=f'turn:{username}',
            request_fingerprint=fingerprint,
            correlation_id=f'corr-{username}',
        )
        repository.terminal(
            result.turn.pk,
            state=TurnState.COMPLETE,
            canonical_result=_canonical_result(),
            workflow_id='analysis_executor',
            evidence_sets=[self._set_spec()],
        )
        turn = ChatTurn.objects.get(pk=result.turn.pk)
        MessageFeedback.objects.create(
            message=turn.output_message,
            user=user,
            rating='up',
            reason='named a sensitive detail',
        )
        for grantee_name in grants:
            ChatThreadGrant.objects.create(
                thread=thread, grantee=self.make_user(grantee_name), granted_by=user
            )
        if voice:
            self._add_voice(user, thread.pk)
        if proposals:
            self._add_proposals(user, thread.pk)
        return user, repository, thread

    def _add_voice(self, user, thread_id):
        from voice.models import VoiceSession, VoiceUtterance, VoiceUtteranceType

        session = VoiceSession.objects.create(
            owner=user,
            thread_id=thread_id,
            scope_key='site:main',
            scope_hash='h' * 64,
            policy_version='v1',
        )
        VoiceUtterance.objects.create(
            session=session,
            utterance_type=VoiceUtteranceType.INTERIM_STATUS,
            spoken_summary='Working on it.',
            spoken_summary_hash='a' * 64,
            policy_version='v1',
        )
        return session

    def _add_proposals(self, user, thread_id):
        common = {
            'owner': user,
            'scope_key': 'site:main',
            'scope_hash': 'h' * 64,
            'thread_id': thread_id,
            'action_type': ProposalAction.WORK_ORDER_HOLD,
            'policy_version': 'v1',
            'expires_at': timezone.now() + timedelta(hours=1),
            'reason': 'operator asked to hold pump 2',
            'intent': {'work_order': 41},
            'preview': {'title': 'Hold WO-41'},
        }
        pending = ChatActionProposal.objects.create(
            idempotency_key=f'prop-pending-{thread_id}', **common
        )
        executed = ChatActionProposal.objects.create(
            idempotency_key=f'prop-executed-{thread_id}',
            state=ProposalState.EXECUTED,
            receipt={'ok': True, 'command': 'work_order.hold'},
            **common,
        )
        return pending, executed

    def age_thread(self, thread, days=OLD_DAYS):
        """Backdate a thread's last activity past the retention window."""
        ChatThread.objects.filter(pk=thread.pk).update(updated_at=_old(days))


class ThreadPurgeTests(RetentionEnvMixin, TestCase):
    """The 400-day scheduled purge and the immediate-deletion path."""

    def test_dry_run_matches_destructive_and_mutates_nothing(self):
        """Dry-run counts equal destructive counts, with zero writes."""
        _, _, old_thread = self.build_thread('dry-old')
        self.build_thread('dry-fresh')
        self.age_thread(old_thread)
        RetrievalMiss.objects.create(query='old question', scope_key='site:main')
        RetrievalMiss.objects.update(created_at=_old(DETAIL_OLD_DAYS))
        AIRequestRejection.objects.create(code='pilot_stopped')
        AIRequestRejection.objects.update(created_at=_old(DETAIL_OLD_DAYS))

        before = (
            ChatThread.objects.count(),
            ChatMessage.objects.count(),
            RetrievalMiss.objects.count(),
        )
        dry = retention.run_all(dry_run=True)
        self.assertEqual(
            before,
            (
                ChatThread.objects.count(),
                ChatMessage.objects.count(),
                RetrievalMiss.objects.count(),
            ),
        )
        self.assertEqual(ChatThreadTombstone.objects.count(), 0)
        self.assertEqual(AIRetentionOutbox.objects.count(), 0)
        self.assertIsNone(retention.last_run())

        real = retention.run_all()
        for family in ('threads', 'retrieval_misses', 'rejections'):
            self.assertEqual(dry['families'][family], real['families'][family], family)
        self.assertEqual(real['errors'], {})
        self.assertIsNotNone(retention.last_run())

    def test_400_day_purge_removes_full_graph(self):
        """Expiry removes the full content graph; the canary survives."""
        _, _, old_thread = self.build_thread('expiry-old', voice=True, proposals=True)
        _, _, fresh_thread = self.build_thread('expiry-fresh')
        self.age_thread(old_thread)

        report = retention.purge_expired_threads()

        self.assertEqual(report, {'threads': 1})
        self.assertFalse(ChatThread.objects.filter(pk=old_thread.pk).exists())
        self.assertEqual(ChatMessage.objects.filter(thread_id=old_thread.pk).count(), 0)
        self.assertEqual(ChatTurn.objects.filter(thread_id=old_thread.pk).count(), 0)
        self.assertEqual(
            ChatEvidenceSet.objects.filter(turn__thread_id=old_thread.pk).count(), 0
        )
        tombstone = ChatThreadTombstone.objects.get(thread_id=old_thread.pk)
        self.assertEqual(tombstone.reason, retention.TOMBSTONE_RETENTION_EXPIRY)
        self.assertEqual(tombstone.message_count, 2)  # prompt + output
        self.assertEqual(tombstone.turn_count, 1)
        # The canary survives fully.
        self.assertTrue(ChatThread.objects.filter(pk=fresh_thread.pk).exists())
        self.assertEqual(
            ChatMessage.objects.filter(thread_id=fresh_thread.pk).count(), 2
        )
        self.assertEqual(ChatEvidenceSetMember.objects.count(), 2)

    def test_immediate_delete_transfers_grant_audit_to_tombstones(self):
        """Grant audit survives thread purge as tombstone rows."""
        user, repository, thread = self.build_thread(
            'grant-owner', grants=('grant-a', 'grant-b')
        )
        revoked_at = timezone.now() - timedelta(days=3)
        first = ChatThreadGrant.objects.filter(thread=thread).order_by('pk').first()
        ChatThreadGrant.objects.filter(pk=first.pk).update(revoked_at=revoked_at)

        repository.delete(thread.pk)

        self.assertFalse(ChatThread.objects.filter(pk=thread.pk).exists())
        self.assertEqual(ChatThreadGrant.objects.count(), 0)
        tombstone = ChatThreadTombstone.objects.get(thread_id=thread.pk)
        self.assertEqual(tombstone.reason, retention.TOMBSTONE_USER_DELETE)
        self.assertTrue(tombstone.had_grants)
        self.assertEqual(tombstone.deleted_by_id, user.pk)
        stones = {stone.grantee.username: stone for stone in tombstone.grants.all()}
        self.assertEqual(set(stones), {'grant-a', 'grant-b'})
        # The pre-revoked grant keeps its stamp; the live one was closed by
        # the purge itself.
        self.assertEqual(stones['grant-a'].revoked_at, revoked_at)
        self.assertIsNotNone(stones['grant-b'].revoked_at)

    def test_naked_thread_delete_with_grant_raises_protected(self):
        """A bypassing delete crashes instead of dropping audit rows."""
        # The regression pin documenting WHY the retention path exists:
        # grants PROTECT the thread, so any delete path that bypasses the
        # reconciliation crashes instead of silently dropping audit rows.
        _, _, thread = self.build_thread('protect-owner', grants=('protect-b',))
        # assertRaises OUTSIDE atomic: the exception must propagate through
        # the atomic block so it rolls back instead of committing broken.
        with self.assertRaises(ProtectedError), transaction.atomic():
            thread.delete()

    def test_immediate_delete_scrubs_danglers_and_voice(self):
        """Dangling proposals scrub and voice sessions delete with the thread."""
        from voice.models import VoiceSession

        _, repository, thread = self.build_thread(
            'dangle-owner', voice=True, proposals=True
        )
        repository.delete(thread.pk)

        pending = ChatActionProposal.objects.get(
            idempotency_key=f'prop-pending-{thread.pk}'
        )
        executed = ChatActionProposal.objects.get(
            idempotency_key=f'prop-executed-{thread.pk}'
        )
        self.assertEqual(pending.state, ProposalState.EXPIRED)
        self.assertEqual(pending.failure_code, 'thread_deleted')
        for proposal in (pending, executed):
            self.assertEqual(proposal.reason, '')
            self.assertEqual(proposal.intent, {})
            self.assertEqual(proposal.preview, {})
        # The receipt is the record of a real-world effect — retained.
        self.assertEqual(executed.receipt['command'], 'work_order.hold')
        self.assertEqual(VoiceSession.objects.filter(thread_id=thread.pk).count(), 0)

    def test_terminal_turn_constraint_never_violated(self):
        """Purge deletes rows; surviving terminal turns stay byte-identical."""
        # Purge deletes rows; it never blanks content fields on terminal
        # turns (the aichat_turn_terminal_result_state constraint). A
        # surviving terminal turn stays byte-identical.
        _, _, old_thread = self.build_thread('constraint-old')
        _, _, fresh_thread = self.build_thread('constraint-fresh')
        self.age_thread(old_thread)
        canary = ChatTurn.objects.get(thread_id=fresh_thread.pk)
        before = (canary.state, canary.canonical_result, canary.completed_at)

        retention.purge_expired_threads()

        canary.refresh_from_db()
        self.assertEqual(
            (canary.state, canary.canonical_result, canary.completed_at), before
        )

    def test_idempotent_rerun_reports_zero(self):
        """A second run finds nothing and touches nothing."""
        _, _, old_thread = self.build_thread('rerun-old')
        self.age_thread(old_thread)
        self.assertEqual(retention.purge_expired_threads(), {'threads': 1})
        self.assertEqual(retention.purge_expired_threads(), {'threads': 0})
        # The tombstone from the first run is untouched by the second.
        self.assertEqual(ChatThreadTombstone.objects.count(), 1)

    def test_tombstones_purge_after_400_days(self):
        """Tombstones and their grant children expire on their own clock."""
        _, repository, thread = self.build_thread('stone-owner', grants=('stone-b',))
        repository.delete(thread.pk)
        ChatThreadTombstone.objects.update(deleted_at=_old())
        report = retention.purge_tombstones()
        self.assertGreaterEqual(report['tombstones'], 1)
        self.assertEqual(ChatThreadTombstone.objects.count(), 0)
        self.assertEqual(ChatThreadGrantTombstone.objects.count(), 0)


class VoicePurgeTests(RetentionEnvMixin, TestCase):
    """PROTECT-chain ordering for the voice 400-day family."""

    def _build_capture(self, username, *, state, revisions=3):
        """One capture with a supersedes chain, acceptance, and pin."""
        from voice.models import (
            VoiceCaptureSession,
            VoiceTranscriptAcceptance,
            VoiceTranscriptRevision,
        )

        user = self.make_user(username)
        capture = VoiceCaptureSession.objects.create(
            owner=user,
            scope_key='site:main',
            scope_hash='h' * 64,
            purpose='closeout',
            target_work_order_id=41,
            target_version=1,
            state=state,
            consent_version='v1',
            consented_at=timezone.now(),
            policy_version='v1',
            idempotency_key=f'cap-{username}',
        )
        previous = None
        for index in range(1, revisions + 1):
            previous = VoiceTranscriptRevision.objects.create(
                capture=capture,
                revision=index,
                full_text=f'transcript revision {index}',
                content_hash=f'{index:064d}',
                supersedes=previous,
                created_by=user,
            )
        VoiceTranscriptAcceptance.objects.create(
            revision=previous, accepted_by=user, content_hash=previous.content_hash
        )
        capture.accepted_revision = previous
        capture.save(update_fields=['accepted_revision', 'updated_at'])
        VoiceCaptureSession.objects.filter(pk=capture.pk).update(updated_at=_old())
        return capture

    def test_naive_revision_queryset_delete_raises_protected(self):
        """The self-PROTECT chain defeats a single queryset delete."""
        from voice.models import VoiceTranscriptRevision

        capture = self._build_capture('naive-user', state='committed')
        with self.assertRaises(ProtectedError), transaction.atomic():
            VoiceTranscriptRevision.objects.filter(capture=capture).delete()

    def test_protect_chains_delete_in_order(self):
        """Leaf-first ordering clears the chain; in-flight captures survive."""
        from voice.models import (
            VoiceCaptureSession,
            VoiceSession,
            VoiceTranscriptAcceptance,
            VoiceTranscriptRevision,
        )

        settled = self._build_capture('chain-user', state='committed')
        active = self._build_capture('active-user', state='active')
        user = self.make_user('session-user')
        session = VoiceSession.objects.create(
            owner=user,
            thread_id='thread_gone',
            scope_key='site:main',
            scope_hash='h' * 64,
            policy_version='v1',
            state='ended',
            ended_at=timezone.now(),
        )
        VoiceSession.objects.filter(pk=session.pk).update(ended_at=_old())

        report = retention.purge_expired_voice()

        self.assertFalse(VoiceCaptureSession.objects.filter(pk=settled.pk).exists())
        self.assertEqual(
            VoiceTranscriptRevision.objects.filter(capture_id=settled.pk).count(), 0
        )
        self.assertFalse(VoiceSession.objects.filter(pk=session.pk).exists())
        # An in-flight capture is never touched, however old.
        self.assertTrue(VoiceCaptureSession.objects.filter(pk=active.pk).exists())
        self.assertTrue(
            VoiceTranscriptAcceptance.objects.filter(
                revision__capture_id=active.pk
            ).exists()
        )
        self.assertGreaterEqual(report['voice_captures'], 1)


class DetailFamilyTests(RetentionEnvMixin, TestCase):
    """The 90-day detail purges and the aggregate-before-scrub protocol."""

    def _usage_blob(self, total=100):
        """One usage+soak metadata payload."""
        return {
            'usage': {
                'totals': {
                    'input_tokens': total,
                    'output_tokens': total // 2,
                    'cached_input_tokens': total // 4,
                    'total_tokens': total + total // 2,
                },
                'events': [
                    {
                        'source': 'chat',
                        'input_tokens': total,
                        'output_tokens': total // 2,
                        'cached_input_tokens': total // 4,
                        'total_tokens': total + total // 2,
                    }
                ],
            },
            'evidence_gate': {'verdict': 'pass'},
        }

    def _add_usage_messages(self, thread, *, count=3, days=150):
        # 150, not 91: the scrub is month-grained and only touches months
        # whose ENTIRE span is past the 90-day cutoff.
        rows = []
        for index in range(count):
            rows.append(
                ChatMessage.objects.create(
                    thread=thread,
                    sequence=100 + index,
                    role='assistant',
                    content=f'answer {index}',
                    metadata=self._usage_blob(),
                )
            )
        ChatMessage.objects.filter(pk__in=[row.pk for row in rows]).update(
            created_at=_old(days)
        )
        return rows

    def test_usage_scrub_aggregates_before_removing_detail(self):
        """Aggregates commit before detail leaves; soak blobs survive."""
        user, _, thread = self.build_thread('usage-owner')
        old_rows = self._add_usage_messages(thread, count=3, days=150)
        fresh = ChatMessage.objects.create(
            thread=thread,
            sequence=200,
            role='assistant',
            content='fresh',
            metadata=self._usage_blob(),
        )

        report = retention.scrub_usage_detail()

        self.assertEqual(report['usage_messages'], 3)
        totals = AIUsageMonthlyAggregate.objects.get(
            source='turn_usage', user_id=user.pk, dimension=''
        )
        self.assertEqual(totals.turn_count, 3)
        self.assertEqual(totals.input_tokens, 300)
        self.assertEqual(totals.total_tokens, 450)
        per_source = AIUsageMonthlyAggregate.objects.get(
            source='turn_usage', user_id=user.pk, dimension='chat'
        )
        self.assertEqual(per_source.input_tokens, 300)
        for row in old_rows:
            row.refresh_from_db()
            self.assertNotIn('usage', row.metadata)
            # Soak evidence survives to the 400-day thread purge.
            self.assertEqual(row.metadata['evidence_gate'], {'verdict': 'pass'})
        fresh.refresh_from_db()
        self.assertIn('usage', fresh.metadata)

    def test_usage_scrub_rerun_after_partial_failure(self):
        """A crash between aggregate and scrub reruns consistently."""
        user, _, thread = self.build_thread('crash-owner')
        rows = self._add_usage_messages(thread, count=3, days=150)
        # First run: aggregates commit, then scrubbing "crashes" partway.
        with mock.patch.object(
            retention, '_scrub_usage_rows', side_effect=RuntimeError('crash')
        ):
            with self.assertRaises(RuntimeError):
                retention.scrub_usage_detail()
        aggregates_after_crash = AIUsageMonthlyAggregate.objects.count()
        self.assertGreater(aggregates_after_crash, 0)
        # Simulate a partial scrub before the crash.
        first = rows[0]
        first.metadata.pop('usage')
        first.save(update_fields=['metadata'])

        retention.scrub_usage_detail()

        # No recomputation from partially-scrubbed data: the aggregate rows
        # are exactly the ones the first (pre-crash) pass wrote.
        self.assertEqual(
            AIUsageMonthlyAggregate.objects.count(), aggregates_after_crash
        )
        totals = AIUsageMonthlyAggregate.objects.get(
            source='turn_usage', user_id=user.pk, dimension=''
        )
        self.assertEqual(totals.turn_count, 3)
        for row in rows:
            row.refresh_from_db()
            self.assertNotIn('usage', row.metadata)

    def test_90_day_row_families(self):
        """Misses, rejections, and settled reservations purge at 90 days."""
        user = self.make_user('detail-user')
        RetrievalMiss.objects.create(query='old', scope_key='site:main')
        RetrievalMiss.objects.update(created_at=_old(DETAIL_OLD_DAYS))
        RetrievalMiss.objects.create(query='fresh', scope_key='site:main')
        AIRequestRejection.objects.create(code='pilot_stopped')
        AIRequestRejection.objects.update(created_at=_old(DETAIL_OLD_DAYS))
        AIRequestRejection.objects.create(code='quota_exhausted')

        expires = timezone.now() + timedelta(minutes=5)
        settled = AIQuotaReservation.objects.create(
            idempotency_key='res-settled',
            user=user,
            purpose='turn',
            reserved_tokens=1000,
            settled_tokens=900,
            state=AIQuotaReservationState.SETTLED,
            expires_at=expires,
        )
        reserved = AIQuotaReservation.objects.create(
            idempotency_key='res-live',
            user=user,
            purpose='turn',
            reserved_tokens=500,
            state=AIQuotaReservationState.RESERVED,
            expires_at=expires,
        )
        AIQuotaReservation.objects.filter(pk__in=[settled.pk, reserved.pk]).update(
            created_at=_old(DETAIL_OLD_DAYS + 40)
        )

        audit_keep = AIQuotaAuditEvent.objects.create(
            action=AIQuotaAuditAction.ASSIGNED
        )
        audit_purge = AIQuotaAuditEvent.objects.create(
            action=AIQuotaAuditAction.REVOKED
        )
        AIQuotaAuditEvent.objects.filter(pk=audit_keep.pk).update(
            created_at=_old(DETAIL_OLD_DAYS)
        )
        AIQuotaAuditEvent.objects.filter(pk=audit_purge.pk).update(created_at=_old())

        self.assertEqual(retention.purge_retrieval_misses(), {'retrieval_misses': 1})
        self.assertEqual(RetrievalMiss.objects.get().query, 'fresh')
        self.assertEqual(retention.purge_request_rejections(), {'rejections': 1})
        self.assertEqual(AIRequestRejection.objects.get().code, 'quota_exhausted')

        self.assertEqual(
            retention.purge_quota_reservations(), {'quota_reservations': 1}
        )
        # RESERVED rows are never purged (the reconciliation task owns them).
        self.assertEqual(
            AIQuotaReservation.objects.get().state, AIQuotaReservationState.RESERVED
        )
        aggregate = AIUsageMonthlyAggregate.objects.get(
            source='quota_reservation', user_id=user.pk, dimension='turn'
        )
        self.assertEqual(aggregate.reserved_tokens, 1000)
        self.assertEqual(aggregate.settled_tokens, 900)

        # Quota management audit is a 400-day class, not 90.
        self.assertEqual(retention.purge_quota_audit_events(), {'quota_audit': 1})
        self.assertEqual(AIQuotaAuditEvent.objects.get().pk, audit_keep.pk)

    def test_batching_boundary(self):
        """More rows than the batch size purge fully across batches."""
        for index in range(12):
            RetrievalMiss.objects.create(query=f'q{index}', scope_key='site:main')
        RetrievalMiss.objects.update(created_at=_old(DETAIL_OLD_DAYS))
        report = retention.purge_retrieval_misses(batch_size=5)
        self.assertEqual(report['retrieval_misses'], 12)
        self.assertEqual(RetrievalMiss.objects.count(), 0)

    def test_aggregates_purge_after_thirteen_months(self):
        """The sanitized aggregate store has its own 13-month clock."""
        AIUsageMonthlyAggregate.objects.create(
            month=(timezone.now() - timedelta(days=430)).date().replace(day=1),
            source='turn_usage',
            user_id=1,
        )
        AIUsageMonthlyAggregate.objects.create(
            month=timezone.now().date().replace(day=1), source='turn_usage', user_id=1
        )
        self.assertEqual(retention.purge_usage_aggregates(), {'usage_aggregates': 1})
        self.assertEqual(AIUsageMonthlyAggregate.objects.count(), 1)

    def test_aggregate_clock_is_calendar_months(self):
        """Exactly thirteen months old stays; one month older goes; no day drift."""
        from datetime import date

        self.assertEqual(retention._months_before(date(2026, 9, 5), 13), date(2025, 8, 1))
        self.assertEqual(retention._months_before(date(2026, 1, 31), 13), date(2024, 12, 1))
        today = timezone.now().date()
        keep = retention._months_before(today, 13)
        drop = retention._months_before(today, 14)
        AIUsageMonthlyAggregate.objects.create(month=keep, source='turn_usage', user_id=1)
        AIUsageMonthlyAggregate.objects.create(month=drop, source='turn_usage', user_id=1)
        self.assertEqual(retention.purge_usage_aggregates(), {'usage_aggregates': 1})
        self.assertEqual(
            list(AIUsageMonthlyAggregate.objects.values_list('month', flat=True)), [keep]
        )


class UploadSweepTests(RetentionEnvMixin, TestCase):
    """The 24-hour ai_uploads TTL and the orphan/outbox reconciliation."""

    def setUp(self):
        """Point MEDIA_ROOT at a scratch directory."""
        self.media_root = tempfile.mkdtemp(prefix='retention-media-')
        self.override = override_settings(MEDIA_ROOT=self.media_root)
        self.override.enable()
        self.addCleanup(self.override.disable)

    def _upload(self, dirname, filename, *, age_hours):
        """One upload file with a controlled age."""
        target = retention._upload_root() / dirname
        target.mkdir(parents=True, exist_ok=True)
        path = target / filename
        path.write_text('payload', encoding='utf-8')
        stamp = timezone.now().timestamp() - age_hours * 3600
        os.utime(path, (stamp, stamp))
        return path

    def test_ttl_constant_matches_the_upload_contract(self):
        """The service TTL pins the upload contract value."""
        self.assertEqual(retention.UPLOAD_TTL_HOURS, 24)

    def test_ttl_sweep_and_orphan_outbox(self):
        """Stale files unlink; orphan dirs ride the outbox to deletion."""
        _, _, thread = self.build_thread('upload-owner')
        stale = self._upload(thread.pk, 'stale.pdf', age_hours=25)
        fresh = self._upload(thread.pk, 'fresh.pdf', age_hours=1)
        orphan = self._upload('thread_orphan123', 'old.pdf', age_hours=30)

        counts = retention.sweep_upload_dirs()

        self.assertFalse(stale.exists())
        self.assertTrue(fresh.exists())
        self.assertFalse(orphan.exists())  # TTL removed the file itself
        self.assertEqual(counts['removed'], 2)
        self.assertEqual(counts['orphans'], 1)
        outbox = AIRetentionOutbox.objects.get(kind='upload_dir', state='pending')
        self.assertEqual(outbox.reference, 'thread_orphan123')

        drained = retention.process_retention_outbox()
        self.assertEqual(drained['done'], 1)
        self.assertFalse((retention._upload_root() / 'thread_orphan123').exists())

    def test_enqueue_is_idempotent(self):
        """Duplicate pending targets collapse to one outbox row."""
        retention.enqueue_outbox('upload_dir', 'thread_x')
        retention.enqueue_outbox('upload_dir', 'thread_x')
        self.assertEqual(AIRetentionOutbox.objects.count(), 1)

    def test_outbox_backoff_and_permanent_failure(self):
        """Failures back off, then fail permanently at the attempt cap."""
        target = retention._upload_root() / 'thread_locked'
        target.mkdir(parents=True, exist_ok=True)
        retention.enqueue_outbox('upload_dir', 'thread_locked')

        with mock.patch.object(
            retention.shutil, 'rmtree', side_effect=OSError('denied')
        ):
            report = retention.process_retention_outbox()
        self.assertEqual(report, {'done': 0, 'retried': 1, 'failed_permanent': 0})
        row = AIRetentionOutbox.objects.get()
        self.assertEqual(row.attempts, 1)
        self.assertEqual(row.last_error_code, 'OSError')
        self.assertGreater(row.next_attempt_at, timezone.now())

        # Not due yet: a second drain leaves it alone.
        self.assertEqual(
            retention.process_retention_outbox(),
            {'done': 0, 'retried': 0, 'failed_permanent': 0},
        )

        # At the attempt cap the row fails permanently (the ops metric).
        AIRetentionOutbox.objects.filter(pk=row.pk).update(
            attempts=retention.OUTBOX_MAX_ATTEMPTS - 1,
            next_attempt_at=timezone.now() - timedelta(minutes=1),
        )
        with mock.patch.object(
            retention.shutil, 'rmtree', side_effect=OSError('denied')
        ):
            report = retention.process_retention_outbox()
        self.assertEqual(report['failed_permanent'], 1)
        row.refresh_from_db()
        self.assertEqual(row.state, 'failed_permanent')

    def test_missing_target_is_success(self):
        """A vanished target completes the outbox row."""
        retention.enqueue_outbox('upload_dir', 'thread_never_existed')
        report = retention.process_retention_outbox()
        self.assertEqual(report['done'], 1)
        self.assertEqual(AIRetentionOutbox.objects.get().state, 'done')


class GateAndExclusionTests(RetentionEnvMixin, TestCase):
    """Flag gating of the scheduled task and the excluded-classes pin."""

    def test_scheduled_task_noops_while_dark(self):
        """The daily task purges nothing until the flag is enabled."""
        from aichat import tasks

        _, _, old_thread = self.build_thread('gate-old')
        self.age_thread(old_thread)

        with override_settings(FEATURE_AI_RETENTION_JOBS=False):
            tasks.run_retention_purge()
        self.assertTrue(ChatThread.objects.filter(pk=old_thread.pk).exists())

        with override_settings(FEATURE_AI_RETENTION_JOBS=True):
            tasks.run_retention_purge()
        self.assertFalse(ChatThread.objects.filter(pk=old_thread.pk).exists())

    def test_excluded_classes_survive_a_full_run(self):
        """Latch and quota-policy rows are never purge candidates."""
        from aichat.models import AIPilotStopLatch, AIQuotaPolicy, AIQuotaProfile

        latch = AIPilotStopLatch.objects.create(reason_code='manual')
        AIPilotStopLatch.objects.filter(pk=latch.pk).update(engaged_at=_old())
        policy = AIQuotaPolicy.objects.create(
            profile=AIQuotaProfile.STANDARD
            if hasattr(AIQuotaProfile, 'STANDARD')
            else AIQuotaProfile.choices[0][0],
            version=1,
            user_daily_tokens=1,
            tenant_daily_tokens=1,
            deployment_daily_tokens=1,
            requests_per_minute=1,
            requests_per_hour=1,
        )
        AIQuotaPolicy.objects.filter(pk=policy.pk).update(created_at=_old())

        report = retention.run_all()

        self.assertEqual(report['errors'], {})
        self.assertTrue(AIPilotStopLatch.objects.filter(pk=latch.pk).exists())
        self.assertTrue(AIQuotaPolicy.objects.filter(pk=policy.pk).exists())
