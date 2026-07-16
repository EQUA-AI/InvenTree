"""Tests for closeout capture, revision, decision, and voice-handoff commands."""

from django.test import TestCase, override_settings
from django.utils import timezone

from tasks.closeout_models import (
    CloseoutCapture,
    CloseoutCaptureStatus,
    CloseoutFieldDecision,
    CloseoutProposal,
    CloseoutSourceType,
)
from tasks.models import WorkOrderEvent, WorkOrderLifecycle
from tasks.services.closeout_capture import (
    CaptureError,
    CaptureStaleRevision,
    DecisionRequired,
    VoiceHandoffUnavailable,
    abandon_capture,
    accept_voice_handoff,
    create_capture,
    record_decisions,
    revise_capture,
)
from tasks.services.work_orders import StaleVersion
from tasks.tests.closeout_fixtures import CLOSEOUT_FLAGS, CloseoutEnvMixin


@override_settings(**CLOSEOUT_FLAGS)
class CloseoutCaptureTest(CloseoutEnvMixin, TestCase):
    """Capture lifecycle: create, revise, abandon, replay, staleness."""

    def setUp(self):
        self.build_env(username='capture-user')

    def create(self, key='cap-1', narrative='Replaced the filter, flow restored.'):
        return create_capture(
            work_order_id=self.work_order.pk,
            actor=self.actor,
            narrative=narrative,
            expected_version=self.work_order.lifecycle_version,
            idempotency_key=key,
        )

    def test_create_capture_records_source_and_revision(self):
        result = self.create()
        capture = CloseoutCapture.objects.get(pk=result.metadata['capture_id'])
        self.assertEqual(capture.status, CloseoutCaptureStatus.OPEN)
        self.assertEqual(capture.source_type, CloseoutSourceType.TYPED)
        self.assertEqual(capture.current_revision.revision, 1)
        self.assertEqual(
            capture.current_revision.work_order_version,
            self.work_order.lifecycle_version,
        )
        self.assertTrue(
            WorkOrderEvent.objects.filter(
                work_order=self.work_order, event_type='CLOSEOUT_CAPTURE_CREATED'
            ).exists()
        )

    def test_create_is_idempotent_on_replay(self):
        first = self.create(key='same')
        replay = self.create(key='same')
        self.assertEqual(first.event_id, replay.event_id)
        self.assertEqual(CloseoutCapture.objects.count(), 1)

    def test_second_active_capture_is_rejected(self):
        self.create(key='one')
        with self.assertRaises(CaptureError):
            self.create(key='two')

    def test_stale_version_is_rejected(self):
        with self.assertRaises(StaleVersion):
            create_capture(
                work_order_id=self.work_order.pk,
                actor=self.actor,
                narrative='text',
                expected_version=self.work_order.lifecycle_version + 5,
                idempotency_key='stale',
            )

    def test_blank_narrative_is_rejected(self):
        with self.assertRaises(CaptureError):
            self.create(key='blank', narrative='   ')

    @override_settings(AIMMS_CLOSEOUT_MAX_NARRATIVE_CHARS=10)
    def test_oversized_narrative_is_rejected(self):
        with self.assertRaises(CaptureError):
            self.create(key='big', narrative='x' * 11)

    @override_settings(AIMMS_CLOSEOUT_WIZARD_ENABLED=False)
    def test_disabled_wizard_fails_closed(self):
        with self.assertRaises(CaptureError):
            self.create(key='off')

    def test_ineligible_lifecycle_is_rejected(self):
        self.work_order.lifecycle_status = WorkOrderLifecycle.PLANNED
        self.work_order.save(update_fields=['lifecycle_status'])
        with self.assertRaises(CaptureError):
            self.create(key='planned')

    def test_revise_appends_immutable_revision(self):
        created = self.create()
        capture_id = created.metadata['capture_id']
        revise_capture(
            work_order_id=self.work_order.pk,
            capture_id=capture_id,
            actor=self.actor,
            narrative='Corrected: replaced filter and seal.',
            expected_revision=1,
            expected_version=self.work_order.lifecycle_version,
            idempotency_key='revise-1',
        )
        capture = CloseoutCapture.objects.get(pk=capture_id)
        self.assertEqual(capture.current_revision.revision, 2)
        self.assertEqual(capture.status, CloseoutCaptureStatus.OPEN)
        self.assertEqual(capture.revisions.count(), 2)
        first = capture.revisions.get(revision=1)
        self.assertEqual(first.narrative, 'Replaced the filter, flow restored.')
        self.assertEqual(capture.current_revision.supersedes_id, first.pk)

    def test_revise_against_stale_revision_is_rejected(self):
        created = self.create()
        with self.assertRaises(CaptureStaleRevision):
            revise_capture(
                work_order_id=self.work_order.pk,
                capture_id=created.metadata['capture_id'],
                actor=self.actor,
                narrative='newer',
                expected_revision=7,
                expected_version=self.work_order.lifecycle_version,
                idempotency_key='revise-stale',
            )

    def test_abandon_frees_the_active_slot(self):
        created = self.create()
        abandon_capture(
            work_order_id=self.work_order.pk,
            capture_id=created.metadata['capture_id'],
            actor=self.actor,
            expected_version=self.work_order.lifecycle_version,
            idempotency_key='abandon-1',
            reason='wrong ticket',
        )
        capture = CloseoutCapture.objects.get(pk=created.metadata['capture_id'])
        self.assertEqual(capture.status, CloseoutCaptureStatus.ABANDONED)
        self.create(key='after-abandon')


@override_settings(**CLOSEOUT_FLAGS)
class CloseoutDecisionTest(CloseoutEnvMixin, TestCase):
    """Manual review path: every promoted field records a human decision."""

    def setUp(self):
        self.build_env(username='decision-user')
        result = create_capture(
            work_order_id=self.work_order.pk,
            actor=self.actor,
            narrative='Replaced pump seal after leak.',
            expected_version=self.work_order.lifecycle_version,
            idempotency_key='cap-dec',
        )
        self.capture_id = result.metadata['capture_id']

    def decide(self, decisions, key='dec-1'):
        return record_decisions(
            work_order_id=self.work_order.pk,
            capture_id=self.capture_id,
            actor=self.actor,
            decisions=decisions,
            expected_version=self.work_order.lifecycle_version,
            idempotency_key=key,
        )

    def test_manual_decisions_reach_reviewed_when_required_covered(self):
        result = self.decide([
            {'field_path': 'action', 'decision': 'edited', 'final_value': 'Replaced seal'},
            {'field_path': 'result', 'decision': 'edited', 'final_value': 'Leak stopped'},
            {
                'field_path': 'verification_summary',
                'decision': 'edited',
                'final_value': 'No drips over 10 minutes',
            },
        ])
        capture = CloseoutCapture.objects.get(pk=self.capture_id)
        self.assertEqual(capture.status, CloseoutCaptureStatus.REVIEWED)
        self.assertEqual(result.metadata['missing_required_fields'], [])
        proposal = CloseoutProposal.objects.get(pk=result.metadata['proposal_id'])
        self.assertEqual(proposal.extractor, 'manual')
        self.assertEqual(
            CloseoutFieldDecision.objects.filter(proposal=proposal).count(), 3
        )
        for row in proposal.decisions.all():
            self.assertEqual(row.origin, 'manual')

    def test_partial_decisions_stay_proposed_with_missing_list(self):
        result = self.decide([
            {'field_path': 'action', 'decision': 'edited', 'final_value': 'Replaced seal'}
        ])
        capture = CloseoutCapture.objects.get(pk=self.capture_id)
        self.assertEqual(capture.status, CloseoutCaptureStatus.PROPOSED)
        self.assertIn('result', result.metadata['missing_required_fields'])

    def test_accept_without_extracted_value_is_rejected(self):
        with self.assertRaises(DecisionRequired):
            self.decide([{'field_path': 'action', 'decision': 'accepted'}])

    def test_unknown_field_is_rejected(self):
        with self.assertRaises(DecisionRequired):
            self.decide([
                {'field_path': 'evil', 'decision': 'edited', 'final_value': 'x'}
            ])

    def test_blank_required_value_does_not_count_as_covered(self):
        self.decide([
            {'field_path': 'action', 'decision': 'edited', 'final_value': '  '},
            {'field_path': 'result', 'decision': 'edited', 'final_value': 'ok'},
            {
                'field_path': 'verification_summary',
                'decision': 'edited',
                'final_value': 'ok',
            },
        ])
        capture = CloseoutCapture.objects.get(pk=self.capture_id)
        self.assertEqual(capture.status, CloseoutCaptureStatus.PROPOSED)


@override_settings(**CLOSEOUT_FLAGS)
class VoiceHandoffTest(CloseoutEnvMixin, TestCase):
    """The #8 -> #15 contract: exact accepted revision, snapshot once."""

    def setUp(self):
        self.build_env(username='voice-user')

    def build_voice_capture(self, *, accepted=True, text='Motor bearing replaced.'):
        import hashlib

        from voice.models import (
            CapturePurpose,
            CaptureState,
            VoiceCaptureSession,
            VoiceTranscriptAcceptance,
            VoiceTranscriptRevision,
        )

        capture = VoiceCaptureSession.objects.create(
            owner=self.actor,
            scope_key='test-scope',
            scope_hash='h' * 64,
            purpose=CapturePurpose.CLOSEOUT,
            target_work_order_id=self.work_order.pk,
            target_version=self.work_order.lifecycle_version,
            state=CaptureState.ACCEPTED if accepted else CaptureState.ACTIVE,
            consent_version='v1',
            consented_at=timezone.now(),
            policy_version='p1',
            idempotency_key=f'voice-{self.work_order.pk}',
        )
        revision = VoiceTranscriptRevision.objects.create(
            capture=capture,
            revision=1,
            full_text=text,
            content_hash=hashlib.sha256(text.encode()).hexdigest(),
            created_by=self.actor,
        )
        if accepted:
            VoiceTranscriptAcceptance.objects.create(
                revision=revision,
                accepted_by=self.actor,
                content_hash=revision.content_hash,
            )
            capture.accepted_revision = revision
            capture.save(update_fields=['accepted_revision'])
        return capture, revision

    def test_handoff_snapshots_exact_revision_once(self):
        voice_capture, revision = self.build_voice_capture()
        closeout_capture = accept_voice_handoff(voice_capture=voice_capture)
        self.assertEqual(closeout_capture.source_type, CloseoutSourceType.VOICE)
        self.assertEqual(closeout_capture.transcript_reference, str(revision.pk))
        self.assertEqual(
            closeout_capture.current_revision.narrative, 'Motor bearing replaced.'
        )
        self.assertEqual(
            closeout_capture.current_revision.source_content_hash,
            revision.content_hash,
        )
        replay = accept_voice_handoff(voice_capture=voice_capture)
        self.assertEqual(replay.pk, closeout_capture.pk)
        self.assertEqual(CloseoutCapture.objects.count(), 1)

    def test_voice_snapshot_cannot_be_revised(self):
        voice_capture, _revision = self.build_voice_capture()
        closeout_capture = accept_voice_handoff(voice_capture=voice_capture)
        with self.assertRaises(CaptureError):
            revise_capture(
                work_order_id=self.work_order.pk,
                capture_id=closeout_capture.pk,
                actor=self.actor,
                narrative='rewritten',
                expected_revision=1,
                expected_version=self.work_order.lifecycle_version,
                idempotency_key='voice-revise',
            )

    def test_missing_acceptance_is_rejected(self):
        voice_capture, revision = self.build_voice_capture(accepted=False)
        voice_capture.accepted_revision = revision
        with self.assertRaises(CaptureError):
            accept_voice_handoff(voice_capture=voice_capture)

    @override_settings(AIMMS_CLOSEOUT_WIZARD_ENABLED=False)
    def test_disabled_wizard_fails_closed_for_voice(self):
        voice_capture, _revision = self.build_voice_capture()
        with self.assertRaises(VoiceHandoffUnavailable):
            accept_voice_handoff(voice_capture=voice_capture)

    def test_voice_service_handoff_commits_capture(self):
        from voice.models import CaptureState
        from voice.services.capture import handoff_capture

        voice_capture, revision = self.build_voice_capture()
        closeout_capture = handoff_capture(capture=voice_capture)
        self.assertEqual(closeout_capture.transcript_reference, str(revision.pk))
        voice_capture.refresh_from_db()
        self.assertEqual(voice_capture.state, CaptureState.COMMITTED)

    @override_settings(AIMMS_CLOSEOUT_WIZARD_ENABLED=False)
    def test_voice_service_handoff_fails_closed_when_disabled(self):
        from voice.models import CaptureState
        from voice.services.capture import DestinationUnavailable, handoff_capture

        voice_capture, _revision = self.build_voice_capture()
        with self.assertRaises(DestinationUnavailable):
            handoff_capture(capture=voice_capture)
        voice_capture.refresh_from_db()
        self.assertEqual(voice_capture.state, CaptureState.ACCEPTED)
