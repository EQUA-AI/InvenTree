"""O15: exact accepted bytes, attributed revisions, original media and scope."""

import hashlib
import json
import os
import tempfile
import uuid
from datetime import datetime
from unittest.mock import Mock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.utils import timezone

from rest_framework.test import APIClient

from tasks.closeout_models import CloseoutReading
from tasks.closeout_serializers import CloseoutCaptureSerializer
from tasks.models import WorkOrderCloseout
from tasks.report_models import AcceptedCloseoutReport, CaptureOriginal
from tasks.serializers import WorkOrderOverviewSerializer
from tasks.services.accepted_reports import report_url
from tasks.services.capture_originals import original_summary, retain_original
from tasks.services.closeout import complete_work_order
from tasks.services.closeout_amend import decide_amendment, propose_amendment
from tasks.services.closeout_capture import accept_voice_handoff
from tasks.tests.closeout_fixtures import (
    CLOSEOUT_FLAGS,
    VALID_CLOSEOUT,
    CloseoutEnvMixin,
)
from voice.models import VoiceCaptureSession
from voice.services.capture import accept_revision, append_revision


@override_settings(
    **CLOSEOUT_FLAGS,
    AIMMS_CLOSEOUT_AMENDMENTS_ENABLED=True,
    AIMMS_CLOSEOUT_AMENDMENT_APPROVAL='self',
)
class AcceptedReportTests(CloseoutEnvMixin, TestCase):
    """Disposable media and SQLite only; no external storage or services."""

    def setUp(self):
        """Create authorized Work and local temporary media."""
        self.build_env(username='o15-report')
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        storage = override_settings(MEDIA_ROOT=scratch.name)
        storage.enable()
        self.addCleanup(storage.disable)
        self.client = APIClient()
        self.client.force_authenticate(self.actor)

    def complete(self, capture_id=None):
        """Use the real acceptance boundary."""
        return complete_work_order(
            work_order_id=self.work_order.pk,
            actor=self.actor,
            expected_version=self.work_order.lifecycle_version,
            idempotency_key='accepted-report',
            closeout=VALID_CLOSEOUT,
            capture_id=capture_id,
        )

    def test_manual_completion_does_not_attribute_abandoned_or_unused_stories(self):
        """Retained history is not a source of a manually completed account."""
        from tasks.closeout_models import CloseoutCapture, CloseoutProposal
        from tasks.services.closeout_capture import abandon_capture, create_capture

        first = create_capture(
            work_order_id=self.work_order.pk,
            actor=self.actor,
            narrative='Abandoned and unrelated story',
            expected_version=self.work_order.lifecycle_version,
            idempotency_key='abandoned-source',
        )
        capture = CloseoutCapture.objects.get(pk=first.metadata['capture_id'])
        CloseoutProposal.objects.create(
            capture_revision=capture.current_revision,
            extractor='synthetic',
            schema_version=1,
            content_hash='c' * 64,
            fields={},
            part_candidates=[],
            warnings=[],
        )
        abandon_capture(
            work_order_id=self.work_order.pk,
            actor=self.actor,
            capture_id=capture.pk,
            expected_version=self.work_order.lifecycle_version,
            idempotency_key='abandon-source',
            reason='Unrelated story',
        )
        unused = create_capture(
            work_order_id=self.work_order.pk,
            actor=self.actor,
            narrative='Unused story',
            expected_version=self.work_order.lifecycle_version,
            idempotency_key='unused-source',
        )
        from tasks.services.closeout_capture import record_decisions

        record_decisions(
            work_order_id=self.work_order.pk,
            actor=self.actor,
            capture_id=unused.metadata['capture_id'],
            expected_version=self.work_order.lifecycle_version,
            idempotency_key='unused-decisions',
            decisions=[
                {
                    'field_path': key,
                    'decision': 'edited',
                    'final_value': VALID_CLOSEOUT[key],
                }
                for key in ('action', 'result', 'verification_summary')
            ],
        )
        self.complete()
        report = AcceptedCloseoutReport.objects.get()
        self.assertEqual(report.document['captures'], [])
        self.assertEqual(report.document['proposals'], [])
        self.assertEqual(
            {row['status'] for row in report.document['other_captures']},
            {'abandoned', 'reviewed'},
        )
        self.assertNotIn('Story source: Capture', report.text)
        self.assertIn('Other retained stories (not account sources)', report.text)
        self.assertIn(f'Capture {capture.pk} · Status abandoned', report.text)

    def test_completion_freezes_only_its_consumed_capture_proposals(self):
        """The account source matches the native completed_closeout relation."""
        from tasks.closeout_models import CloseoutCapture
        from tasks.services.closeout_capture import create_capture, record_decisions

        created = create_capture(
            work_order_id=self.work_order.pk,
            actor=self.actor,
            narrative='The actual completed story',
            expected_version=self.work_order.lifecycle_version,
            idempotency_key='source',
        )
        capture_id = created.metadata['capture_id']
        record_decisions(
            work_order_id=self.work_order.pk,
            actor=self.actor,
            capture_id=capture_id,
            expected_version=self.work_order.lifecycle_version,
            idempotency_key='source-decisions',
            decisions=[
                {
                    'field_path': key,
                    'decision': 'edited',
                    'final_value': VALID_CLOSEOUT[key],
                }
                for key in ('action', 'result', 'verification_summary')
            ],
        )
        self.complete(capture_id=capture_id)
        capture = CloseoutCapture.objects.get(pk=capture_id)
        report = AcceptedCloseoutReport.objects.get()
        self.assertEqual(
            [row['id'] for row in report.document['captures']], [capture_id]
        )
        self.assertEqual(report.document['other_captures'], [])
        self.assertEqual(
            [row['id'] for row in report.document['proposals']],
            list(capture.current_revision.proposals.values_list('pk', flat=True)),
        )
        self.assertIn(f'Story source: Capture {capture_id}', report.text)

    def test_verifiable_download_matches_hash_and_contains_exact_document_and_text(
        self,
    ):
        """The advertised digest is reproducible from downloaded UTF-8 bytes."""
        from part.models import Part
        from stock.models import StockItem
        from tasks.closeout_models import CloseoutPartUsage

        part = Part.objects.create(name='Recorded report material')
        stock = StockItem.objects.create(part=part, quantity=2)
        stock.take_stock(1, self.actor, notes='Accepted Work account')
        tracking = stock.tracking_info.order_by('-pk').first()
        usage = CloseoutPartUsage.objects.create(
            work_order=self.work_order,
            part=part,
            stock_item=stock,
            stock_tracking_id=tracking.pk,
            used_quantity=1,
            disposition='installed',
            source='walkup',
            state='reconciled',
        )
        self.work_order.title = 'Synthetic 測定'
        self.work_order.save()
        self.complete()
        report = AcceptedCloseoutReport.objects.get()
        self.assertEqual(report.document['materials'][0]['id'], usage.pk)
        self.assertEqual(report.document['materials'][0]['stock_item'], stock.pk)
        self.assertEqual(
            report.document['materials'][0]['stock_tracking_id'], tracking.pk
        )
        # O24 shares the frozen Work document. It cannot change with the
        # reader's stock role or grant, nor authorize live Stock resources.
        reader = self.make_scoped_user('report-content-viewer')
        reader.maintenance_scopes = set()
        self.client.force_authenticate(reader)
        with self.settings(AIMMS_MAINTENANCE_SCOPE_RESOLVER=None):
            response = self.client.get(report_url(report))
            live = self.client.get(
                f'/api/tasks/work-orders/{self.work_order.pk}/closeout/part-usage/'
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(live.status_code, 200)
        self.assertIsNone(live.data[0]['stock_item'])
        self.assertIsNone(live.data[0]['stock_tracking_id'])
        exported = response.data['canonical_export'].encode('utf-8')
        self.assertEqual(hashlib.sha256(exported).hexdigest(), report.content_hash)
        self.assertEqual(
            json.loads(exported), {'document': report.document, 'text': report.text}
        )

    @override_settings(AIMMS_CLOSEOUT_VERIFY_POLICY='off')
    def test_completer_is_not_claimed_as_acceptor_and_native_verification_is_separate(
        self,
    ):
        """A later native verification does not rewrite completion's snapshot."""
        from tasks.services.closeout_amend import verify_closeout

        self.complete()
        report = AcceptedCloseoutReport.objects.get()
        before = (report.document, report.text, report.content_hash)
        response = self.client.get(report_url(report)).data
        self.assertEqual(response['completed_by'], self.actor.pk)
        self.assertIsNone(response['verified_by'])
        self.assertIsNone(response['verified_at'])
        self.assertIn(f'Completed by {self.actor.username}', report.text)
        self.assertIn('Acceptance: Not recorded', report.text)
        self.assertNotIn('Accepted by', report.text)
        supervisor = self.make_scoped_user(
            'native-verifier', permissions=['verify_closeout']
        )
        self.work_order.refresh_from_db()
        with self.settings(AIMMS_CLOSEOUT_VERIFY_POLICY='supervisor'):
            verify_closeout(
                work_order_id=self.work_order.pk,
                actor=supervisor,
                expected_version=self.work_order.lifecycle_version,
                idempotency_key='native-verification',
            )
        response = self.client.get(report_url(report)).data
        self.assertEqual(response['verified_by'], supervisor.pk)
        self.assertEqual(response['verified_by_name'], supervisor.username)
        self.assertIsNotNone(response['verified_at'])
        self.work_order.refresh_from_db()
        overview = WorkOrderOverviewSerializer.get_structured_closeout(self.work_order)
        self.assertEqual(overview['verified_by'], supervisor.pk)
        self.assertEqual(overview['verified_by_name'], supervisor.username)
        report.refresh_from_db()
        self.assertEqual((report.document, report.text, report.content_hash), before)
        self.work_order.refresh_from_db()
        proposed = propose_amendment(
            work_order_id=self.work_order.pk,
            actor=self.actor,
            expected_version=self.work_order.lifecycle_version,
            idempotency_key='verified-amend',
            changes={'result': 'Clarified result'},
            reason='Synthetic correction after native verification',
        )
        decide_amendment(
            work_order_id=self.work_order.pk,
            actor=self.actor,
            expected_version=self.work_order.lifecycle_version,
            idempotency_key='verified-apply',
            amendment_id=proposed.metadata['amendment_id'],
            approve=True,
        )
        revised = AcceptedCloseoutReport.objects.get(revision=2)
        self.assertIn(f'Verified by {supervisor.username}', revised.text)
        self.assertIn(f'Completed by {self.actor.username}', revised.text)
        self.assertNotIn('Accepted by', revised.text)

    @override_settings(AIMMS_CAPTURE_MAX_BYTES=0)
    def test_default_media_cap_reads_native_upload_policy_above_fifty_mebibytes(self):
        """Read bounds follow native upload policy, not a hidden 50 MiB limit."""
        file = Mock(name='recording-upload')
        file.name = 'recording.webm'
        file.read.return_value = b'original'
        with patch('common.settings.get_global_setting', return_value=80):
            retain_original(work_order=self.work_order, actor=self.actor, file=file)
        file.read.assert_called_once_with(80 * 1024 * 1024 + 1)

    def artifact_with_attachment(self, *, kind, payload):
        """Use the native artifact command on an explicitly missing stored file."""
        from common.models import Attachment
        from tasks.investigation_models import InvestigationRequest

        request = InvestigationRequest.objects.create(
            work_order=self.work_order,
            purpose='Synthetic',
            next_check='Observe',
            required_artifacts=[],
            created_by=self.actor,
            updated_by=self.actor,
        )
        attachment = Attachment.objects.create(
            model_type='workorder',
            model_id=self.work_order.pk,
            upload_user=self.actor,
            attachment='missing-native-attachment.txt',
        )
        return request, {
            'work_order_id': self.work_order.pk,
            'actor': self.actor,
            'expected_version': self.work_order.lifecycle_version,
            'request_id': request.pk,
            'request_version': request.version,
            'operation': 'artifact',
            'idempotency_key': 'missing-file',
            'fields': {
                'kind': kind,
                'payload': payload,
                'attachment_id': attachment.pk,
            },
        }

    def test_nonrecording_report_attachment_does_not_open_or_copy_original(self):
        """Ordinary native attachment references keep their existing behavior."""
        request, arguments = self.artifact_with_attachment(
            kind='report',
            payload={
                'description': 'Retained report reference',
                'source': 'Synthetic',
                'status': 'pending',
            },
        )
        self.observation_command(**arguments)
        self.assertEqual(request.artifacts.count(), 1)
        self.assertFalse(CaptureOriginal.objects.exists())

    def test_missing_observation_original_is_validation_refusal_with_atomic_rollback(
        self,
    ):
        """Missing source bytes produce a recoverable refusal, never a server error."""
        from rest_framework.exceptions import ValidationError

        request, arguments = self.artifact_with_attachment(
            kind='observation', payload={'text': 'Synthetic'}
        )
        with self.assertRaises(ValidationError):
            self.observation_command(**arguments)
        self.assertFalse(request.artifacts.exists())
        self.assertFalse(CaptureOriginal.objects.exists())

    def test_recording_timing_on_report_requests_a_retained_original(self):
        """Explicit timing opts other artifact kinds into recording retention."""
        from rest_framework.exceptions import ValidationError

        request, arguments = self.artifact_with_attachment(
            kind='report',
            payload={
                'description': 'Recording report',
                'source': 'Synthetic',
                'status': 'pending',
                'recording_duration_ms': 0,
            },
        )
        with self.assertRaises(ValidationError):
            self.observation_command(**arguments)
        self.assertFalse(request.artifacts.exists())

    def voice(self):
        """Create a consented capture for this exact Work."""
        return VoiceCaptureSession.objects.create(
            owner=self.actor,
            scope_key='o15',
            scope_hash='a' * 64,
            purpose='closeout',
            target_work_order_id=self.work_order.pk,
            target_version=self.work_order.lifecycle_version,
            consent_version='closeout-originals-v1',
            consented_at=timezone.now(),
            policy_version='o15',
            idempotency_key=str(uuid.uuid4()),
        )

    def observation_command(self, **kwargs):
        """Resolve this fixture's explicit scope after native actor refresh."""
        from tasks.services.investigation_requests import command

        scopes = self.actor.maintenance_scopes
        with self.settings(
            AIMMS_MAINTENANCE_SCOPE_RESOLVER=lambda actor: (
                scopes if actor.pk == self.actor.pk else set()
            )
        ):
            return command(**kwargs)

    def test_migration_matches_models(self):
        """The reserved local migration must represent the actual model state."""
        call_command('makemigrations', 'tasks', check=True, dry_run=True, verbosity=0)

    def test_completion_freezes_full_sources_and_both_representations_hash(self):
        """Readings can later change without rewriting either export."""
        reading = CloseoutReading.objects.create(
            work_order=self.work_order,
            label='Synthetic observation',
            raw_text='unknown',
            recorded_by=self.actor,
            normalization_rule_version='test',
        )
        self.complete()
        frozen = AcceptedCloseoutReport.objects.get()
        before = (frozen.document, frozen.text, frozen.content_hash)
        self.assertEqual(frozen.document['readings'][0]['raw_text'], 'unknown')
        self.assertIsNone(frozen.document['person_work_time'])
        self.assertEqual(frozen.accepted_by, self.actor)
        digest = hashlib.sha256(
            json.dumps(
                {'document': frozen.document, 'text': frozen.text},
                sort_keys=True,
                separators=(',', ':'),
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        self.assertEqual(frozen.content_hash, digest)
        reading.raw_text = 'later correction'
        reading.save()
        self.work_order.title = 'Later title'
        self.work_order.save()
        frozen.refresh_from_db()
        self.assertEqual((frozen.document, frozen.text, frozen.content_hash), before)
        self.assertEqual(self.client.get(report_url(frozen)).data['text'], before[1])

    def test_completion_replay_creates_one_snapshot(self):
        """A timeout retry cannot create another accepted revision."""
        self.complete()
        self.complete()
        self.assertEqual(AcceptedCloseoutReport.objects.count(), 1)

    def test_snapshot_failure_rolls_back_completion(self):
        """Acceptance cannot succeed if its immutable export failed."""
        with (
            patch(
                'tasks.services.accepted_reports.freeze_report',
                side_effect=RuntimeError,
            ),
            self.assertRaises(RuntimeError),
        ):
            self.complete()
        self.assertFalse(WorkOrderCloseout.objects.exists())
        self.work_order.refresh_from_db()
        self.assertEqual(self.work_order.lifecycle_status, 'verifying')

    def test_amendment_appends_and_retains_original_sources_and_attribution(self):
        """A revision names its predecessor, requester and accepting actor."""
        self.complete()
        original = AcceptedCloseoutReport.objects.get()
        before = (original.document, original.text, original.content_hash)
        self.work_order.refresh_from_db()
        proposed = propose_amendment(
            work_order_id=self.work_order.pk,
            actor=self.actor,
            expected_version=self.work_order.lifecycle_version,
            idempotency_key='amend',
            changes={'result': 'Clarified result'},
            reason='Synthetic correction',
        )
        decide_amendment(
            work_order_id=self.work_order.pk,
            actor=self.actor,
            expected_version=self.work_order.lifecycle_version,
            idempotency_key='apply',
            amendment_id=proposed.metadata['amendment_id'],
            approve=True,
        )
        original.refresh_from_db()
        revised = AcceptedCloseoutReport.objects.get(revision=2)
        self.assertEqual(
            (original.document, original.text, original.content_hash), before
        )
        self.assertEqual(revised.previous_id, original.pk)
        self.assertEqual(revised.document['closeout']['result'], 'Clarified result')
        self.assertEqual(revised.document['readings'], original.document['readings'])
        self.assertEqual(revised.document['amendment']['requested_by'], self.actor.pk)
        self.assertEqual(revised.accepted_by_id, self.actor.pk)
        overview = WorkOrderOverviewSerializer.get_structured_closeout(self.work_order)
        self.assertEqual(
            [r['id'] for r in overview['reports']], [original.pk, revised.pk]
        )

    def test_legacy_completion_is_not_backfilled_on_read(self):
        """Missing history stays missing."""
        WorkOrderCloseout.objects.create(
            work_order=self.work_order,
            completed_by=self.actor,
            completed_at=timezone.now(),
            content_hash='a' * 64,
            **VALID_CLOSEOUT,
        )
        self.assertEqual(
            WorkOrderOverviewSerializer.get_structured_closeout(self.work_order)[
                'reports'
            ],
            [],
        )
        self.assertFalse(AcceptedCloseoutReport.objects.exists())

    def test_model_and_bulk_mutation_are_rejected(self):
        """Normal ORM edit/delete paths cannot rewrite frozen history."""
        self.complete()
        report = AcceptedCloseoutReport.objects.get()
        for operation in (
            report.save,
            report.delete,
            lambda: AcceptedCloseoutReport.objects.update(text='changed'),
            lambda: AcceptedCloseoutReport.objects.all().delete(),
        ):
            with self.assertRaises(ValueError):
                operation()

    def test_original_exact_bytes_timing_and_hash_survive_voice_handoff(self):
        """Transcript edits reference the same retained original, never replace it."""
        voice = self.voice()
        started = datetime.fromisoformat('2026-10-20T10:00:00+03:00')
        original = retain_original(
            work_order=self.work_order,
            actor=self.actor,
            voice_capture=voice,
            file=SimpleUploadedFile(
                'original.webm', b'original audio bytes', content_type='audio/webm'
            ),
            content_type='audio/webm',
            started_at=started,
            duration_ms=1200,
        )
        revision = append_revision(
            capture=voice,
            full_text='Reviewed story',
            created_by=self.actor,
            segments=[{'start_ms': 0, 'end_ms': 1200, 'text': 'Reviewed story'}],
        )
        accept_revision(
            capture=voice,
            revision_id=revision.pk,
            content_hash=revision.content_hash,
            accepted_by=self.actor,
        )
        capture = accept_voice_handoff(voice_capture=voice)
        body = CloseoutCaptureSerializer(capture).data
        self.assertEqual(body['source_spans'][0]['original_id'], original.pk)
        self.assertEqual(
            body['source_spans'][0]['transcript_revision'], str(revision.pk)
        )
        self.assertEqual(body['originals'][0]['duration_ms'], 1200)
        response = self.client.get(body['originals'][0]['url'])
        self.assertEqual(b''.join(response.streaming_content), b'original audio bytes')
        self.assertEqual(
            original.content_hash, hashlib.sha256(b'original audio bytes').hexdigest()
        )
        self.assertNotIn(original.file.name, json.dumps(body, default=str))

    @patch.dict(os.environ, {'AIMMS_SINGLE_SITE_POLICY_KEY': 'o15'})
    def test_upload_is_owner_bound_replayable_and_no_replacement(self):
        """The same original replays; another file is refused before acceptance."""
        voice = self.voice()
        url = f'/api/tasks/work-orders/{self.work_order.pk}/capture-media/'

        def upload(data=b'original'):
            return self.client.post(
                url,
                {
                    'file': SimpleUploadedFile('recording.webm', data),
                    'voice_capture': voice.pk,
                    'expected_version': self.work_order.lifecycle_version,
                },
                format='multipart',
            )

        first = upload()
        self.assertEqual(first.status_code, 201, first.data)
        self.assertIsNone(first.data['started_at'])
        self.assertIsNone(first.data['duration_ms'])
        self.assertEqual(upload().data['id'], first.data['id'])
        self.assertEqual(upload(b'replacement').status_code, 400)
        self.assertEqual(CaptureOriginal.objects.count(), 1)
        voice.owner = self.make_scoped_user('different-owner')
        voice.save()
        self.assertEqual(upload().status_code, 404)

    def test_shared_reports_survive_client_revocation_but_machine_only_ones_do_not(self):
        """Originals and reports follow the current Work boundary on every read."""
        self.complete()
        report = AcceptedCloseoutReport.objects.get()
        original = retain_original(
            work_order=self.work_order,
            actor=self.actor,
            file=SimpleUploadedFile('capture.bin', b'bytes'),
        )
        media = (
            f'/api/tasks/work-orders/{self.work_order.pk}/capture-media/{original.pk}/'
        )
        outsider = self.make_scoped_user('outsider')
        outsider.maintenance_scopes = set()
        self.client.force_authenticate(outsider)
        self.assertEqual(self.client.get(report_url(report)).status_code, 200)
        shared_media = self.client.get(media)
        self.assertEqual(shared_media.status_code, 200)
        self.assertEqual(b''.join(shared_media.streaming_content), b'bytes')
        self.work_order.customer = None
        self.work_order.save(update_fields=['customer'])
        self.assertEqual(self.client.get(report_url(report)).status_code, 404)
        self.assertEqual(self.client.get(media).status_code, 404)
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(report_url(report)).status_code, (401, 403))
        self.assertIn(self.client.get(media).status_code, (401, 403))

    @override_settings(AIMMS_CAPTURE_MAX_BYTES=3)
    def test_oversize_upload_leaves_no_original(self):
        """Bounds are checked before writing storage."""
        with self.assertRaises(ValueError):
            retain_original(
                work_order=self.work_order,
                actor=self.actor,
                file=SimpleUploadedFile('large.bin', b'1234'),
            )
        self.assertFalse(CaptureOriginal.objects.exists())

    def test_observation_retains_attachment_bytes_and_supplied_timing(self):
        """The existing scoped artifact command seals original attachment media."""
        from common.models import Attachment
        from tasks.investigation_models import InvestigationRequest

        request = InvestigationRequest.objects.create(
            work_order=self.work_order,
            purpose='Synthetic check',
            next_check='Observe',
            required_artifacts=[],
            created_by=self.actor,
            updated_by=self.actor,
        )
        attachment = Attachment.objects.create(
            model_type='workorder',
            model_id=self.work_order.pk,
            upload_user=self.actor,
            attachment=SimpleUploadedFile('observation.txt', b'original observation'),
        )
        fields = {
            'kind': 'observation',
            'attachment_id': attachment.pk,
            'payload': {
                'text': 'Synthetic observation',
                'recording_started_at': '2026-10-20T07:00:00Z',
                'recording_duration_ms': 0,
            },
        }
        arguments = {
            'work_order_id': self.work_order.pk,
            'actor': self.actor,
            'expected_version': self.work_order.lifecycle_version,
            'request_version': request.version,
            'request_id': request.pk,
            'operation': 'artifact',
            'idempotency_key': 'observation-media',
            'fields': fields,
        }
        result = self.observation_command(**arguments)
        json.dumps(result)  # Shared command results must stay JSON primitives.
        self.assertEqual(self.observation_command(**arguments), result)
        original = CaptureOriginal.objects.get()
        self.assertEqual(original.duration_ms, 0)
        self.assertEqual(
            original_summary(original)['started_at'], '2026-10-20T07:00:00+00:00'
        )
        attachment.attachment.save(
            'changed.txt', SimpleUploadedFile('changed.txt', b'changed')
        )
        with original.file.open('rb') as stream:
            self.assertEqual(stream.read(), b'original observation')

    @patch.dict(os.environ, {'AIMMS_SINGLE_SITE_POLICY_KEY': 'o15'})
    def test_upload_stale_and_accepted_sessions_cannot_attach_later_originals(self):
        """Originals cannot be added after the exact accepted transcript freezes."""
        voice = self.voice()
        url = f'/api/tasks/work-orders/{self.work_order.pk}/capture-media/'
        values = {
            'file': SimpleUploadedFile('r.webm', b'bytes'),
            'voice_capture': voice.pk,
            'expected_version': 999,
        }
        self.assertEqual(
            self.client.post(url, values, format='multipart').status_code, 409
        )
        voice.state = 'accepted'
        voice.save()
        values.update(
            file=SimpleUploadedFile('r.webm', b'bytes'),
            expected_version=self.work_order.lifecycle_version,
        )
        self.assertEqual(
            self.client.post(url, values, format='multipart').status_code, 400
        )
        self.assertFalse(CaptureOriginal.objects.exists())

    def test_original_is_private_until_voice_commit_then_uses_current_work_scope(self):
        """An authorized Work reader cannot guess an unshared voice original."""
        self.complete()
        report = AcceptedCloseoutReport.objects.get()
        voice = self.voice()
        original = retain_original(
            work_order=self.work_order,
            actor=self.actor,
            voice_capture=voice,
            file=SimpleUploadedFile('private.webm', b'private original'),
        )
        reader = self.make_scoped_user('scoped-reader', permissions=['view_workorder'])
        self.client.force_authenticate(reader)
        self.assertEqual(self.client.get(report_url(report)).status_code, 200)
        url = (
            f'/api/tasks/work-orders/{self.work_order.pk}/capture-media/{original.pk}/'
        )
        self.assertEqual(self.client.get(url).status_code, 404)
        voice.state = 'committed'
        voice.save()
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content), b'private original')

    def test_retained_original_protects_voice_revision_during_existing_purge(self):
        """Native retention reports a protected root without losing its transcript."""
        from aichat.services.voice_retention import purge_captures

        voice = self.voice()
        revision = append_revision(
            capture=voice, full_text='Retained source story', created_by=self.actor
        )
        accept_revision(
            capture=voice,
            revision_id=revision.pk,
            content_hash=revision.content_hash,
            accepted_by=self.actor,
        )
        retain_original(
            work_order=self.work_order,
            actor=self.actor,
            voice_capture=voice,
            file=SimpleUploadedFile('retained.webm', b'original'),
        )
        result = purge_captures(
            VoiceCaptureSession.objects.filter(pk=voice.pk), batch_size=10
        )
        self.assertEqual(result['voice_captures_blocked'], 1)
        self.assertEqual(result['voice_captures'], 0)
        voice.refresh_from_db()
        self.assertEqual(voice.accepted_revision_id, revision.pk)
        self.assertEqual(voice.accepted_revision.full_text, 'Retained source story')

    @patch.dict(os.environ, {'AIMMS_SINGLE_SITE_POLICY_KEY': 'o15'})
    def test_client_recording_time_requires_timezone_and_unknown_is_not_zero(self):
        """Neither capture path guesses the timezone of a client timestamp."""
        from rest_framework.exceptions import ValidationError

        from tasks.investigation_serializers import ArtifactFieldsSerializer

        voice = self.voice()
        response = self.client.post(
            f'/api/tasks/work-orders/{self.work_order.pk}/capture-media/',
            {
                'file': SimpleUploadedFile('original.webm', b'original'),
                'voice_capture': voice.pk,
                'expected_version': self.work_order.lifecycle_version,
                'started_at': '2026-10-20T07:00:00',
            },
            format='multipart',
        )
        self.assertEqual(response.status_code, 400)
        serializer = ArtifactFieldsSerializer(
            data={
                'kind': 'observation',
                'payload': {
                    'text': 'Synthetic',
                    'recording_started_at': '2026-10-20T07:00:00',
                },
            }
        )
        with self.assertRaises(ValidationError):
            serializer.is_valid(raise_exception=True)
        self.assertFalse(CaptureOriginal.objects.exists())

    @patch.dict(os.environ, {'AIMMS_SINGLE_SITE_POLICY_KEY': 'o15'})
    def test_original_start_preserves_client_instant_in_both_native_timezone_modes(
        self,
    ):
        """Server timezone and the native SQLite test mode cannot shift the source."""
        for use_tz in (False, True):
            with (
                self.subTest(use_tz=use_tz),
                self.settings(USE_TZ=use_tz, TIME_ZONE='Asia/Riyadh'),
            ):
                voice = self.voice()

                def upload(voice_capture_id=voice.pk):
                    return self.client.post(
                        f'/api/tasks/work-orders/{self.work_order.pk}/capture-media/',
                        {
                            'file': SimpleUploadedFile('original.webm', b'original'),
                            'voice_capture': voice_capture_id,
                            'expected_version': self.work_order.lifecycle_version,
                            'started_at': '2026-10-20T12:30:00+05:30',
                            'duration_ms': 0,
                        },
                        format='multipart',
                    )

                result = upload()
                self.assertEqual(result.status_code, 201, result.data)
                self.assertEqual(result.data['started_at'], '2026-10-20T07:00:00+00:00')
                original = CaptureOriginal.objects.get(pk=result.data['id'])
                self.assertEqual(
                    original_summary(original)['started_at'], result.data['started_at']
                )
                self.assertEqual(upload().data['id'], original.pk)

    @patch.dict(os.environ, {'AIMMS_SINGLE_SITE_POLICY_KEY': 'o15'})
    def test_legacy_transcript_consent_cannot_store_original_media(self):
        """A text-only acceptance never silently authorizes recorded audio."""
        voice = self.voice()
        voice.consent_version = 'consent-v3-memory'
        voice.save()
        response = self.client.post(
            f'/api/tasks/work-orders/{self.work_order.pk}/capture-media/',
            {
                'file': SimpleUploadedFile('original.webm', b'original'),
                'voice_capture': voice.pk,
                'expected_version': self.work_order.lifecycle_version,
            },
            format='multipart',
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data['code'], 'ORIGINAL_MEDIA_CONSENT_REQUIRED')
        self.assertFalse(CaptureOriginal.objects.exists())

    @patch.dict(
        os.environ,
        {
            'AIMMS_SINGLE_SITE_POLICY_KEY': 'o15',
            'AIMMS_VOICE_CAPTURE_ENABLED': '1',
            'AIMMS_VOICE_PURPOSES': 'closeout',
        },
    )
    def test_closeout_media_consent_is_durable_and_purpose_bound(self):
        """The new disclosure version is stored without changing live consent."""
        values = {
            'purpose': 'closeout',
            'consent_version': 'closeout-originals-v1',
            'work_order_id': self.work_order.pk,
            'work_order_version': self.work_order.lifecycle_version,
        }
        response = self.client.post('/api/voice/captures/', values, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        capture = VoiceCaptureSession.objects.get(pk=response.data['id'])
        self.assertEqual(capture.consent_version, 'closeout-originals-v1')
        values['purpose'] = 'fault_intake'
        self.assertEqual(
            self.client.post('/api/voice/captures/', values, format='json').status_code,
            400,
        )

    def test_media_collection_and_original_methods_are_separate(self):
        """Unsupported methods return 405 instead of entering the wrong handler."""
        root = f'/api/tasks/work-orders/{self.work_order.pk}/capture-media/'
        self.assertEqual(self.client.get(root).status_code, 405)
        original = retain_original(
            work_order=self.work_order,
            actor=self.actor,
            file=SimpleUploadedFile('source.bin', b'original'),
        )
        self.assertEqual(self.client.post(f'{root}{original.pk}/', {}).status_code, 405)

    @override_settings(AIMMS_CAPTURE_MAX_BYTES=3)
    def test_refused_observation_original_rolls_back_the_artifact(self):
        """A known file-size refusal cannot leave an observation without its media."""
        from rest_framework.exceptions import ValidationError

        from common.models import Attachment
        from tasks.investigation_models import InvestigationRequest

        request = InvestigationRequest.objects.create(
            work_order=self.work_order,
            purpose='Synthetic',
            next_check='Observe',
            required_artifacts=[],
            created_by=self.actor,
            updated_by=self.actor,
        )
        attachment = Attachment.objects.create(
            model_type='workorder',
            model_id=self.work_order.pk,
            upload_user=self.actor,
            attachment=SimpleUploadedFile('large.txt', b'1234'),
        )
        with self.assertRaises(ValidationError):
            self.observation_command(
                work_order_id=self.work_order.pk,
                actor=self.actor,
                expected_version=self.work_order.lifecycle_version,
                request_id=request.pk,
                request_version=request.version,
                operation='artifact',
                idempotency_key='oversize-observation',
                fields={
                    'kind': 'observation',
                    'payload': {'text': 'Synthetic'},
                    'attachment_id': attachment.pk,
                },
            )
        self.assertFalse(request.artifacts.exists())
        self.assertFalse(CaptureOriginal.objects.exists())

    @patch.dict(os.environ, {'AIMMS_SINGLE_SITE_POLICY_KEY': 'o15'})
    @patch('common.validators.get_global_setting', return_value=0)
    def test_original_upload_preserves_native_attachment_upload_limit(self, _setting):
        """Original recording uploads cannot bypass the deployment's file policy."""
        voice = self.voice()
        response = self.client.post(
            f'/api/tasks/work-orders/{self.work_order.pk}/capture-media/',
            {
                'file': SimpleUploadedFile('original.webm', b'original'),
                'voice_capture': voice.pk,
                'expected_version': self.work_order.lifecycle_version,
            },
            format='multipart',
        )
        self.assertEqual(response.status_code, 400)
        self.assertFalse(CaptureOriginal.objects.exists())
