"""O20 authorization, revision, date and governed deletion checks offline."""

import hashlib
from datetime import datetime, timezone as dt_timezone
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError as DjangoValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connection
from django.db.migrations.autodetector import MigrationAutodetector
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.state import ProjectState
from django.test import TestCase, override_settings
from django.urls import reverse

from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from common.attachment_revision import attachment_revision
from common.models import Attachment
from company.models import Company
from tasks.calibration_models import CalibrationTraceability
from tasks.closeout_models import CloseoutReading
from tasks.models import WorkOrder, WorkOrderDeletionRecord
from tasks.services.calibration import command, project
from tasks.services.scheduling import NotMutable, delete_work_order, update_work_order_plan
from tasks.services.work_orders import IdempotencyConflict, StaleVersion, WorkOrderScopeError
from tasks.tests.closeout_fixtures import CLOSEOUT_FLAGS, CloseoutEnvMixin


@override_settings(**CLOSEOUT_FLAGS, TIME_ZONE='Asia/Riyadh', USE_TZ=True)
class CalibrationTraceabilityTests(CloseoutEnvMixin, TestCase):
    """Real model/HTTP commands with an isolated SQLite and media directory."""

    def setUp(self):
        """A calibration Work, one native reading, and a native uploaded file."""
        self.build_env(username='calibration')
        self.work_order.work_order_type = 'calibration'
        self.work_order.save()
        self.reading = CloseoutReading.objects.create(
            work_order=self.work_order, label='Reference check', raw_text='0 bar', recorded_by=self.actor
        )
        CloseoutReading.objects.filter(pk=self.reading.pk).update(recorded_at=datetime(2026, 10, 19, 22, tzinfo=dt_timezone.utc))
        self.reading.refresh_from_db()
        self.certificate = Attachment.objects.create(
            model_type='workorder', model_id=self.work_order.pk,
            attachment=SimpleUploadedFile('synthetic-certificate.pdf', b'%PDF-1.4 synthetic certificate', content_type='application/pdf'),
            comment='Synthetic offline fixture',
        )
        self.intent = dict(
            operation='record', expected_version=1, expected_revision=0,
            idempotency_key='calibration-first', reference_instrument_id='REF-001',
            certificate_number='SYN-2026', issuing_lab='Synthetic lab',
            valid_until='2026-10-20', certificate_id=self.certificate.pk,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.actor)

    def apply(self, **overrides):
        """Run one explicit intent through the real service."""
        return command(work_order_id=self.work_order.pk, reading_id=self.reading.pk, actor=self.actor, **{**self.intent, **overrides})

    def url(self, work=None, reading=None):
        """Resolve the same Work-owned route used by the native UI."""
        return reverse('work-calibration-traceability', kwargs={'pk': (work or self.work_order).pk, 'reading': (reading or self.reading).pk})

    def test_record_is_attributed_audited_and_does_not_accept_or_complete(self):
        """A saved record supplies no physical/test/operating acceptance."""
        response = self.client.post(self.url(), self.intent, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        row = response.data['traceability']
        self.assertEqual(row['revision'], 1)
        self.assertEqual(row['recorded_by'], self.actor.pk)
        self.assertEqual(row['reading'], self.reading.pk)
        self.assertTrue(row['certificate_available'])
        self.assertTrue(row['recorded_at'])
        self.assertEqual(self.work_order.events.get().event_type, 'calibration_record')
        self.assertEqual(self.work_order.commands.get().command, 'calibration_traceability')
        self.work_order.refresh_from_db()
        self.assertEqual(self.work_order.lifecycle_status, 'verifying')
        self.assertEqual(self.work_order.lifecycle_version, 1)
        self.assertFalse(hasattr(self.work_order, 'structured_closeout'))

    def test_correction_appends_history_and_stale_correction_is_refused(self):
        """Original facts, certificate and actor remain inspectable."""
        first = self.apply()['traceability']
        second = self.apply(operation='correct', expected_revision=1, idempotency_key='correction', certificate_number='SYN-CORRECTED', correction_reason='Transcription correction')['traceability']
        self.assertEqual(second['revision'], 2)
        self.assertEqual(second['supersedes'], first['id'])
        self.assertEqual(project(self.work_order)[self.reading.pk][1], first)
        original = CalibrationTraceability.objects.get(pk=first['id'])
        self.assertEqual(original.certificate_number, 'SYN-2026')
        with self.assertRaises(ValueError):
            original.save()
        with self.assertRaises(StaleVersion):
            self.apply(operation='correct', expected_revision=1, idempotency_key='stale', correction_reason='Stale intent')
        self.assertEqual(CalibrationTraceability.objects.count(), 2)

    def test_exact_replay_is_stable_even_after_correction_and_conflict_is_refused(self):
        """Lost responses can be retried without appending duplicate revisions."""
        first = self.apply()
        self.assertEqual(self.apply(), first)
        self.apply(operation='correct', expected_revision=1, idempotency_key='correct', correction_reason='Fix lab', issuing_lab='Corrected synthetic lab')
        self.assertEqual(self.apply(), first)
        with self.assertRaises(IdempotencyConflict):
            self.apply(certificate_number='DIFFERENT')
        self.assertEqual(self.work_order.commands.count(), 2)
        self.assertEqual(self.work_order.events.count(), 2)

    def test_validity_uses_reading_local_date_including_boundary_and_unknown(self):
        """The reading is Oct 20 in Riyadh; today's date never supplies validity."""
        for until, expected in [('2026-10-19', 'expired_at_reading'), ('2026-10-20', 'within_date_at_reading'), ('2026-10-21', 'within_date_at_reading'), (None, 'unknown')]:
            with self.subTest(until=until):
                result = self.apply(valid_until=until, idempotency_key=str(until), operation='record' if CalibrationTraceability.objects.count() == 0 else 'correct', expected_revision=CalibrationTraceability.objects.count(), correction_reason='Date correction')
                self.assertEqual(result['traceability']['validity_state'], expected)
                self.assertEqual(result['traceability']['validity_timezone'], 'Asia/Riyadh')

    def test_invalid_fields_reason_and_versions_make_no_writes(self):
        """Neither ambiguous IDs nor incomplete certificate facts enter the audit."""
        for changes in [dict(reference_instrument_id=' '), dict(issuing_lab=''), dict(certificate_number=''), dict(valid_until='yesterday'), dict(expected_revision=True), dict(expected_version='1'), dict(certificate_id=True), dict(accepted=True)]:
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.apply(**changes)
        with self.assertRaises(StaleVersion):
            self.apply(expected_version=99)
        self.assertEqual(CalibrationTraceability.objects.count(), 0)
        self.assertEqual(self.work_order.events.count(), 0)
        self.apply()
        with self.assertRaises(ValidationError):
            self.apply(operation='correct', expected_revision=1, idempotency_key='no-reason')

    def test_reading_and_certificate_must_belong_to_exact_work(self):
        """Same-scope sibling records and cross-scope IDs cannot be substituted."""
        sibling = WorkOrder.objects.create(title='Sibling', customer=self.customer, work_order_type='calibration')
        sibling_reading = CloseoutReading.objects.create(work_order=sibling, label='Sibling', raw_text='1', recorded_by=self.actor)
        response = self.client.post(self.url(reading=sibling_reading), self.intent, format='json')
        self.assertEqual(response.status_code, 404)
        Attachment.objects.filter(pk=self.certificate.pk).update(model_id=sibling.pk)
        with self.assertRaises(ValidationError):
            self.apply()
        self.assertEqual(CalibrationTraceability.objects.count(), 0)

    def test_link_only_attachment_is_not_an_uploaded_certificate(self):
        """An external URL does not establish storage of the original certificate."""
        link = Attachment.objects.create(model_type='workorder', model_id=self.work_order.pk, link='https://example.invalid/certificate')
        with self.assertRaises(ValidationError):
            self.apply(certificate_id=link.pk)

    def test_viewer_cannot_write_and_revoked_retry_cannot_replay(self):
        """Django's existing execution-record permission is enforced service-side."""
        viewer = self.make_scoped_user('calibration-viewer')
        self.client.force_authenticate(viewer)
        response = self.client.post(self.url(), self.intent, format='json')
        self.assertEqual(response.status_code, 403)
        editor = self.make_scoped_user('calibration-editor', permissions=['capture_closeout'])
        self.actor = editor
        self.client.force_authenticate(editor)
        saved = self.client.post(self.url(), self.intent, format='json')
        self.assertEqual(saved.status_code, 200, saved.data)
        self.assertEqual(saved.data['traceability']['recorded_by'], editor.pk)
        self.apply()
        editor.user_permissions.clear()
        with self.assertRaises(PermissionDenied):
            self.apply()
        self.assertEqual(CalibrationTraceability.objects.count(), 1)

    def test_foreign_work_and_inactive_actor_are_refused(self):
        """No scope disclosure or bypass on exact retries."""
        self.apply()
        foreign = Company.objects.create(name='Foreign', is_customer=True)
        self.work_order.customer = foreign
        self.work_order.save()
        response = self.client.post(self.url(), self.intent, format='json')
        self.assertEqual(response.status_code, 404)
        with self.assertRaises(WorkOrderScopeError):
            self.apply()
        get_user_model().objects.filter(pk=self.actor.pk).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            self.apply()

    def test_anonymous_and_unsupported_http_intents_are_refused(self):
        """Unknown body keys never override the scope-resolved command parents."""
        anonymous = APIClient()
        self.assertIn(anonymous.post(self.url(), self.intent, format='json').status_code, (401, 403))
        response = self.client.post(self.url(), {**self.intent, 'work_order_id': self.work_order.pk + 1}, format='json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(CalibrationTraceability.objects.count(), 0)

    def test_non_calibration_and_disabled_surface_are_refused(self):
        """Routine tasks, readings or reservations never create traceability."""
        self.work_order.work_order_type = 'inspection'
        self.work_order.save()
        response = self.client.post(self.url(), self.intent, format='json')
        self.assertEqual(response.status_code, 400)
        with override_settings(AIMMS_CLOSEOUT_WIZARD_ENABLED=False):
            self.assertEqual(self.client.post(self.url(), self.intent, format='json').status_code, 404)
        self.assertEqual(CalibrationTraceability.objects.count(), 0)

    def test_deleted_certificate_retains_recorded_identity_but_reports_unavailable(self):
        """Native attachment lifecycle cannot silently erase the revision's facts."""
        first = self.apply()['traceability']
        self.certificate.delete()
        row = project(self.work_order)[self.reading.pk][0]
        self.assertIsNone(row['certificate_id'])
        self.assertFalse(row['certificate_available'])
        self.assertIsNone(row['certificate_url'])
        self.assertEqual(row['certificate_snapshot'], first['certificate_snapshot'])

    def test_governed_work_delete_cascades_revisions_and_retains_snapshot(self):
        """Same-Work revision links do not block the native governed deletion."""
        self.apply()
        self.apply(operation='correct', expected_revision=1, idempotency_key='correct', correction_reason='Correction')
        work_pk, reading_pk = self.work_order.pk, self.reading.pk
        result = delete_work_order(work_order_id=work_pk, actor=self.actor, expected_version=1, idempotency_key='delete', reason='Synthetic fixture removal')
        self.assertFalse(WorkOrder.objects.filter(pk=work_pk).exists())
        self.assertEqual(CalibrationTraceability.objects.count(), 0)
        history = WorkOrderDeletionRecord.objects.get(pk=result.deletion_record_id).snapshot['calibration_traceability'][str(reading_pk)]
        self.assertEqual([row['revision'] for row in history], [2, 1])
        self.assertEqual(history[0]['correction_reason'], 'Correction')

    def test_moved_or_replaced_file_is_never_presented_as_original_certificate(self):
        """A generic attachment edit cannot substitute a new or foreign file."""
        self.apply()
        Attachment.objects.filter(pk=self.certificate.pk).update(model_id=self.work_order.pk + 100)
        self.assertFalse(project(self.work_order)[self.reading.pk][0]['certificate_available'])
        Attachment.objects.filter(pk=self.certificate.pk).update(model_id=self.work_order.pk, attachment='attachments/replacement.pdf')
        self.assertIsNone(project(self.work_order)[self.reading.pk][0]['certificate_url'])

    def test_overview_extension_and_read_only_permission_are_explicit(self):
        """Additive v1 reading linkage is separate from acceptance or the task state."""
        from tasks.work_execution.routine import project as routine

        empty = routine(self.work_order, actor=self.actor)
        self.assertEqual(empty['readings'][0]['calibration_traceability'], [])
        self.assertTrue(empty['calibration_traceability_can_record'])
        self.apply()
        rows = routine(self.work_order, actor=self.actor)['readings'][0]['calibration_traceability']
        self.assertEqual(rows[0]['reading'], self.reading.pk)
        viewer = self.make_scoped_user('projection-viewer')
        self.assertFalse(routine(self.work_order, actor=viewer)['calibration_traceability_can_record'])
        self.assertEqual(routine(self.work_order)['rule'], 'routine_work.v1')

    def test_new_verified_reading_does_not_inherit_another_readings_traceability(self):
        """A retest or parsed result cannot stand in for an explicit new record."""
        from tasks.work_execution.routine import project as routine

        self.apply()
        retest = CloseoutReading.objects.create(
            work_order=self.work_order, label='Retest', raw_text='0 bar',
            verification_state='verified', recorded_by=self.actor,
        )
        rows = {row['id']: row for row in routine(self.work_order)['readings']}
        self.assertEqual(len(rows[self.reading.pk]['calibration_traceability']), 1)
        self.assertEqual(rows[retest.pk]['calibration_traceability'], [])
        self.assertEqual(CalibrationTraceability.objects.count(), 1)
        self.assertEqual(self.work_order.events.count(), 1)

    @override_settings(USE_TZ=False)
    def test_native_naive_reading_date_is_compared_without_client_timezone(self):
        """The fork's test-mode naive datetimes retain their server calendar date."""
        CloseoutReading.objects.filter(pk=self.reading.pk).update(
            recorded_at=datetime(2026, 10, 20, 0, 15)
        )
        result = self.apply(valid_until='2026-10-19')['traceability']
        self.assertEqual(result['validity_state'], 'expired_at_reading')
        self.assertEqual(result['validity_timezone'], 'Asia/Riyadh')
        self.assertEqual(result['reading_recorded_at'], '2026-10-20T00:15:00')

    def test_migration_state_matches_model(self):
        """The reserved local migration creates exactly the registered tasks state."""
        from django.apps import apps

        executor = MigrationExecutor(connection)
        changes = MigrationAutodetector(executor.loader.project_state(), ProjectState.from_apps(apps)).changes(graph=executor.loader.graph)
        self.assertNotIn('tasks', changes)


    def test_snapshot_hashes_actual_bytes_and_detects_native_rename_reupload(self):
        """Reusing the original stored path with different bytes is not original evidence."""
        with self.certificate.attachment.open('rb') as source:
            original = source.read()
        original_path = self.certificate.attachment.name
        Attachment.objects.filter(pk=self.certificate.pk).update(file_size=999999)
        first = self.apply()['traceability']
        snapshot = first['certificate_snapshot']
        self.assertEqual(snapshot['sha256'], hashlib.sha256(original).hexdigest())
        self.assertEqual(snapshot['size'], len(original))
        self.assertEqual(first['certificate_integrity_state'], 'unchanged')
        # Exercise the real storage operations, including deletion of the old
        # path. This also covers historical/out-of-band changes outside the API.
        self.certificate.refresh_from_db()
        self.certificate.rename('retained-old.pdf')
        replacement = b'%PDF-1.4 changed certificate'.ljust(len(original), b' ')
        self.assertEqual(len(replacement), len(original))
        self.certificate.attachment = SimpleUploadedFile(original_path.rsplit('/', 1)[-1], replacement, content_type='application/pdf')
        self.certificate.save()
        self.assertEqual(self.certificate.attachment.name, original_path)
        row = project(self.work_order)[self.reading.pk][0]
        self.assertEqual(row['certificate_integrity_state'], 'changed')
        self.assertFalse(row['certificate_available'])
        self.assertIsNone(row['certificate_url'])
        self.assertEqual(row['certificate_snapshot'], snapshot)
        self.assertEqual(CalibrationTraceability.objects.count(), 1)

    def test_attachment_editor_cannot_rename_reupload_or_move_recorded_certificate(self):
        """Work attachment change rights cannot substitute a capture-authorized record."""
        self.apply()
        editor = self.make_scoped_user('attachment-editor', permissions=['view_workorder', 'change_workorder'])
        self.assertFalse(editor.has_perm('tasks.capture_closeout'))
        self.assertTrue(self.certificate.check_permission('change', editor))
        self.client.force_authenticate(editor)
        url = reverse('api-attachment-detail', kwargs={'pk': self.certificate.pk})
        sibling = WorkOrder.objects.create(title='Sibling', customer=self.customer, work_order_type='calibration')
        for changes in [
            {'filename': 'retained-old.pdf'},
            {'attachment': SimpleUploadedFile('synthetic-certificate.pdf', b'%PDF-1.4 replacement', content_type='application/pdf')},
            {'model_id': sibling.pk},
            {'link': 'https://example.invalid/replacement'},
        ]:
            with self.subTest(changes=list(changes)):
                self.certificate.refresh_from_db()
                response = self.client.patch(url, {**changes, 'expected_revision': attachment_revision(self.certificate)}, format='multipart')
                self.assertEqual(response.status_code, 400, response.data)
                self.assertIn('recorded calibration certificate', str(response.data))
        self.certificate.refresh_from_db()
        # The same low-privilege editor can still edit ordinary metadata.
        response = self.client.patch(url, {'comment': 'Metadata only', 'expected_revision': attachment_revision(self.certificate)}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(project(self.work_order)[self.reading.pk][0]['certificate_integrity_state'], 'unchanged')
        self.assertEqual(CalibrationTraceability.objects.count(), 1)
        self.assertEqual(self.work_order.events.count(), 1)

    def test_unreadable_certificate_cannot_be_recorded_and_never_looks_unchanged(self):
        """A failed storage read is unavailable, not a matching empty file."""
        storage = self.certificate.attachment.storage
        with patch.object(storage, 'open', side_effect=OSError('Synthetic storage outage')):
            with self.assertRaises(ValidationError):
                self.apply()
        self.assertEqual(CalibrationTraceability.objects.count(), 0)
        self.assertEqual(self.work_order.events.count(), 0)
        self.apply()
        with patch.object(storage, 'open', side_effect=OSError('Synthetic storage outage')):
            row = project(self.work_order)[self.reading.pk][0]
        self.assertEqual(row['certificate_integrity_state'], 'unavailable')
        self.assertFalse(row['certificate_available'])
        self.assertIsNone(row['certificate_url'])

    def test_legacy_snapshot_without_hash_is_unknown_not_current_identity(self):
        """Earlier records cannot acquire an inferred original byte identity."""
        first = self.apply()['traceability']
        legacy = {key: value for key, value in first['certificate_snapshot'].items() if key not in ('sha256', 'size')}
        CalibrationTraceability.objects.filter(pk=first['id']).update(certificate_snapshot=legacy)
        row = project(self.work_order)[self.reading.pk][0]
        self.assertEqual(row['certificate_integrity_state'], 'identity_not_recorded')
        self.assertFalse(row['certificate_available'])
        self.assertIsNone(row['certificate_url'])
        self.assertEqual(row['certificate_snapshot'], legacy)

    def test_governed_retype_refused_but_other_planning_and_exact_retry_remain_available(self):
        """A planning edit must not hide recorded, including expired, traceability."""
        from tasks.work_execution.routine import project as routine

        self.apply(valid_until='2026-10-19')
        for new_type in ('inspection', 'corrective'):
            with self.subTest(new_type=new_type), self.assertRaises(NotMutable):
                update_work_order_plan(work_order_id=self.work_order.pk, actor=self.actor, expected_version=1, idempotency_key='retype-' + new_type, fields={'work_order_type': new_type})
        self.work_order.refresh_from_db()
        self.assertEqual(self.work_order.work_order_type, 'calibration')
        self.assertEqual(self.work_order.lifecycle_version, 1)
        rows = routine(self.work_order)['readings'][0]['calibration_traceability']
        self.assertEqual(rows[0]['validity_state'], 'expired_at_reading')
        self.assertEqual(self.work_order.events.count(), 1)
        update_work_order_plan(work_order_id=self.work_order.pk, actor=self.actor, expected_version=1, idempotency_key='title-only', fields={'work_order_type': 'calibration', 'title': 'Metadata correction'})
        self.work_order.refresh_from_db()
        self.assertEqual(self.work_order.title, 'Metadata correction')

    def test_exact_calibration_replay_survives_historical_type_change(self):
        """Reauthorize before replay, but do not deny a command already saved."""
        first = self.apply()
        WorkOrder.objects.filter(pk=self.work_order.pk).update(work_order_type='inspection', lifecycle_version=2)
        response = self.client.post(self.url(), self.intent, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data, first)
        with self.assertRaises(ValidationError):
            self.apply(idempotency_key='new-after-retype')
        self.assertEqual(CalibrationTraceability.objects.count(), 1)
        self.assertEqual(self.work_order.events.count(), 1)

    def test_work_without_traceability_can_still_be_retyped(self):
        """The provenance guard does not freeze a type before any record exists."""
        update_work_order_plan(work_order_id=self.work_order.pk, actor=self.actor, expected_version=1, idempotency_key='before-record', fields={'work_order_type': 'inspection'})
        self.work_order.refresh_from_db()
        self.assertEqual(self.work_order.work_order_type, 'inspection')


    def test_earlier_revision_certificate_remains_protected_after_correction(self):
        """An old revision's file is retained even when the latest selects another."""
        self.apply()
        replacement = Attachment.objects.create(
            model_type='workorder', model_id=self.work_order.pk,
            attachment=SimpleUploadedFile('separate-certificate.pdf', b'%PDF-1.4 separate certificate', content_type='application/pdf'),
        )
        self.apply(operation='correct', expected_revision=1, idempotency_key='separate-file', certificate_id=replacement.pk, correction_reason='Separate corrected certificate')
        url = reverse('api-attachment-detail', kwargs={'pk': self.certificate.pk})
        self.certificate.refresh_from_db()
        response = self.client.patch(url, {'filename': 'old-renamed.pdf', 'expected_revision': attachment_revision(self.certificate)}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual([r['certificate_integrity_state'] for r in project(self.work_order)[self.reading.pk]], ['unchanged', 'unchanged'])


    def test_canonical_patch_and_model_save_cannot_bypass_traceability_type_retention(self):
        """Canonical non-command planning edits obey the same persisted invariant."""
        self.apply()
        url = reverse('work-order-detail', kwargs={'pk': self.work_order.pk})
        response = self.client.patch(url, {'work_order_type': 'inspection'}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn('recorded calibration traceability', str(response.data))
        self.work_order.refresh_from_db()
        self.assertEqual(self.work_order.work_order_type, 'calibration')
        self.work_order.work_order_type = 'corrective'
        with self.assertRaises(DjangoValidationError):
            self.work_order.save(update_fields=['work_order_type'])
        self.work_order.refresh_from_db()
        self.assertEqual(self.work_order.work_order_type, 'calibration')
        self.assertEqual(CalibrationTraceability.objects.count(), 1)
        self.assertEqual(self.work_order.events.count(), 1)
