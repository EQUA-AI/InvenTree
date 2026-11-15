"""Paired native source changes, not historical proof or deletion authority.

Only the explicitly named simulated class neutralizes historical attribution.
Native roles/scopes, session authentication, source writers, storage and S5/S7
private bookkeeping are real but use disposable synthetic fixtures throughout.
"""

from integration_state.tests.assertions import assert_literal_bool

from hashlib import sha256
from unittest.mock import patch
from uuid import uuid4

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TransactionTestCase, override_settings
from django.urls import reverse

from assets.models import AssetMachine, Client, ClientScopeGrant
from assets.tests.integration_attribution_test_support import (
    assume_synthetic_attribution,
)
from common.models import Attachment
from integration_state.models import (
    PrivateCopyAttempt,
    PrivateCopyImpact,
    PrivateDeletionRequest,
)
from tasks import integration_s3_details as details
from tasks import integration_s3_documents as documents
from tasks.models import WorkOrder
from tasks.report_models import CaptureOriginal
from tasks.services.capture_originals import retain_original
from tasks.tests import test_integration_s3_copy_db as support
from tasks.workorder_models import WorkOrderDeletionRecord


class NativeChangeFixture(support.SourceCopyFixture):
    """Own ordinary native login and independent source/storage fixture truth."""

    def native_login(self):
        """Authenticate an ordinary synthetic user; never force authentication."""
        password = uuid4().hex
        self.actor.set_password(password)
        self.actor.save(update_fields=['password'])
        self.assertFalse(self.actor.is_superuser)
        self.assertTrue(
            self.client.login(username=self.actor.username, password=password)
        )
        # Password creation invalidates the inherited fixture's earlier hash.
        # Reuse the genuine session just issued by native username/password login.
        self.session = self.client.session

    def delete_role(self, enabled):
        """Provision through the native RuleSet save/permission-sync path."""
        self.rule.can_delete = enabled
        self.rule.save()

    def native_delete(self, work, *, key=' native-change:007 '):
        """Use the unchanged authenticated/scoped native command endpoint."""
        return self.client.post(
            reverse('kanban-command-delete', kwargs={'pk': work.pk}),
            data={
                'expected_version': work.lifecycle_version,
                'idempotency_key': key,
                'reason': 'Disposable synthetic source-change control',
            },
            content_type='application/json',
        )

    def neighbor(self):
        """A separately ungranted native client and independently stored bytes."""
        tenant = Client.objects.create(name='Native neighbor', code='s3-native-b')
        machine = AssetMachine.objects.create(name='Native neighbor', client=tenant)
        work = WorkOrder.objects.create(
            title='Unrelated native Work',
            status='backlog',
            priority='medium',
            machine=machine,
        )
        content = b'unchanged-other-client\x00\xff'
        attachment = Attachment(
            model_type='workorder',
            model_id=work.pk,
            attachment=SimpleUploadedFile('Neighbor.bin', content),
        )
        attachment.save(rebuild=False)
        return tenant, work, attachment, content

    def stored_bytes(self, name):
        """Read independent synthetic storage truth, never a source permission stub."""
        with default_storage.open(name, 'rb') as handle:
            return handle.read()

    def private_snapshot(self):
        """Observe actual committed owner rows without acting as another writer."""
        return {
            model._meta.label: list(model.objects.order_by('pk').values())
            for model in (PrivateDeletionRequest, PrivateCopyImpact, PrivateCopyAttempt)
        }

    def retained_progress(self, **source_changes):
        """Commit through actual public S3 to existing S5/S7 state owners."""
        prepared = self.prepare_copy(source_changes=source_changes)
        request = self.record_request(prepared)
        impact = prepared.record_copy(request)
        progress = prepared.start(request, impact, attempt=uuid4(), max_attempts=2)
        self.assertEqual(prepared.read_progress(request, impact), progress)
        self.assertEqual(progress.progress.status, 'retained')
        for carrier in (request, impact, progress, prepared.assess(request)):
            for flag in (
                'native_authorized',
                'coverage_complete',
                'deletion_applied',
                'erasure_complete',
            ):
                assert_literal_bool(self, getattr(carrier, flag), False)
        return prepared, request, impact, progress

    def refuses_old_progress(self, prepared, request, impact):
        """A lost/changed source cannot authorize replay or erase committed rows."""
        with self.assertRaises(self.copy_module().SourceCopyUnavailableError):
            prepared.read_progress(request, impact)


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
    AIMMS_SINGLE_SITE_CLIENT_CODE='missing-s3-test-client',
)
class RealPolicyNativeChangesTests(NativeChangeFixture, TransactionTestCase):
    """Native authorization positives alongside unpatched historical withholding."""

    def test_native_delete_role_and_audit_do_not_admit_unproven_source_history(self):
        """Native delete authority is neither AI historical nor erasure authority."""
        self.prepare_operations()
        self.native_login()
        work_pk, attachment_pk = self.work.pk, self.attachment.pk
        name = self.attachment.attachment.name
        self.assertTrue(self.grant.__class__.objects.filter(pk=self.grant.pk).exists())
        self.assertIsNone(
            details.read_work_details(
                self.actor, environment='s3-test', work_pk=work_pk
            )
        )
        with patch.object(
            default_storage, 'open', wraps=default_storage.open
        ) as opened:
            self.assertIsNone(
                documents.read_work_document_content(
                    self.actor,
                    environment='s3-test',
                    attachment_pk=attachment_pk,
                    max_bytes=128,
                )
            )
        opened.assert_not_called()
        self.assertEqual(self.native_delete(self.work).status_code, 403)
        self.assertTrue(WorkOrder.objects.filter(pk=work_pk).exists())
        self.assertTrue(default_storage.exists(name))
        self.assertEqual(WorkOrderDeletionRecord.objects.count(), 0)
        self.delete_role(True)
        response = self.native_delete(self.work)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['deleted'])
        record = WorkOrderDeletionRecord.objects.get(work_order_pk=work_pk)
        self.assertEqual(record.actor_id, self.actor.pk)
        self.assertEqual(record.idempotency_key, ' native-change:007 ')
        self.assertFalse(WorkOrder.objects.filter(pk=work_pk).exists())
        self.assertFalse(Attachment.objects.filter(pk=attachment_pk).exists())
        self.assertFalse(default_storage.exists(name))
        self.assertIsNone(
            details.read_work_details(
                self.actor, environment='s3-test', work_pk=work_pk
            )
        )
        # Characterize the existing lookup order, not a newly repaired replay.
        self.assertEqual(self.native_delete(self.work).status_code, 404)
        self.assertEqual(WorkOrderDeletionRecord.objects.count(), 1)
        self.assertEqual(PrivateDeletionRequest.objects.count(), 0)
        self.assertEqual(PrivateCopyImpact.objects.count(), 0)
        self.assertEqual(PrivateCopyAttempt.objects.count(), 0)


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
    AIMMS_SINGLE_SITE_CLIENT_CODE='missing-s3-test-client',
)
@assume_synthetic_attribution
class SimulatedNativeChangesTests(NativeChangeFixture, TransactionTestCase):
    """Historical admission is simulated; native effects/private commits are real."""

    def test_native_work_delete_preserves_private_progress_and_other_client(self):
        """A native deletion is a separate fact, not an inferred private tombstone."""
        self.prepare_operations()
        self.native_login()
        self.delete_role(True)
        _, other, attachment, content = self.neighbor()
        other_row = WorkOrder.objects.filter(pk=other.pk).values().get()
        attachment_row = Attachment.objects.filter(pk=attachment.pk).values().get()
        own_pk, own_attachment_pk = self.work.pk, self.attachment.pk

        def operation():
            prepared, request, impact, _ = self.retained_progress()
            before = self.private_snapshot()
            self.assertEqual(self.native_delete(self.work).status_code, 200)
            self.assertFalse(WorkOrder.objects.filter(pk=own_pk).exists())
            self.assertFalse(Attachment.objects.filter(pk=own_attachment_pk).exists())
            self.assertEqual(
                WorkOrderDeletionRecord.objects.get(work_order_pk=own_pk).actor_id,
                self.actor.pk,
            )
            self.refuses_old_progress(prepared, request, impact)
            self.assertEqual(self.private_snapshot(), before)
            # The same delete role cannot borrow another client's visibility.
            self.assertEqual(self.native_delete(other).status_code, 404)
            self.assertEqual(
                WorkOrder.objects.filter(pk=other.pk).values().get(), other_row
            )
            self.assertEqual(
                Attachment.objects.filter(pk=attachment.pk).values().get(),
                attachment_row,
            )
            self.assertEqual(self.stored_bytes(attachment.attachment.name), content)
            self.assertFalse(
                WorkOrderDeletionRecord.objects.filter(work_order_pk=other.pk).exists()
            )
            self.assertEqual(self.private_snapshot(), before)
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)

    def attachment_deletion(self, *, fail_storage):
        """Pair two real storage outcomes with identical source/private semantics."""
        self.prepare_operations()
        name, attachment_pk = self.attachment.attachment.name, self.attachment.pk
        self.assertEqual(self.stored_bytes(name), self.blob)

        def operation():
            prepared, request, impact, _ = self.retained_progress(
                operation='work_document.content', source_pk=attachment_pk
            )
            before = self.private_snapshot()
            if fail_storage:
                with patch.object(
                    default_storage,
                    'delete',
                    side_effect=OSError('synthetic delete failure'),
                ) as deleted:
                    self.attachment.delete()
                self.assertTrue(
                    any(call.args[0] == name for call in deleted.call_args_list)
                )
            else:
                self.attachment.delete()
            self.assertFalse(Attachment.objects.filter(pk=attachment_pk).exists())
            self.assertTrue(WorkOrder.objects.filter(pk=self.work.pk).exists())
            self.assertEqual(default_storage.exists(name), fail_storage)
            if fail_storage:
                self.assertEqual(self.stored_bytes(name), self.blob)
            self.assertIsNone(
                documents.read_work_document_content(
                    self.actor,
                    environment='s3-test',
                    attachment_pk=attachment_pk,
                    max_bytes=128,
                )
            )
            self.refuses_old_progress(prepared, request, impact)
            self.assertEqual(self.private_snapshot(), before)
            self.assertEqual(WorkOrderDeletionRecord.objects.count(), 0)
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_attachment_row_and_file_removal_do_not_erase_private_copy_progress(self):
        """Physical removal of this fixture file leaves separate retained metadata."""
        self.attachment_deletion(fail_storage=False)

    def test_attachment_row_absence_does_not_hide_failed_file_deletion(self):
        """A swallowed native storage failure leaves independently verified bytes."""
        self.attachment_deletion(fail_storage=True)

    def test_retained_original_refuses_work_delete_before_attachment_cleanup(self):
        """The native retention fence protects Work, attachment, original and audit."""
        self.prepare_operations()
        self.native_login()
        self.delete_role(True)
        original_bytes = b'retained-independent-original\x00\xff'
        original = retain_original(
            work_order=self.work,
            actor=self.actor,
            file=ContentFile(original_bytes, name='Original.bin'),
            content_type='application/octet-stream',
        )
        self.assertEqual(original.content_hash, sha256(original_bytes).hexdigest())
        self.assertEqual(original.byte_size, len(original_bytes))

        def operation():
            prepared, request, impact, progress = self.retained_progress()
            before = self.private_snapshot()
            with patch.object(
                default_storage, 'delete', wraps=default_storage.delete
            ) as deleted:
                response = self.native_delete(self.work)
            self.assertEqual(response.status_code, 409)
            deleted.assert_not_called()
            self.assertTrue(WorkOrder.objects.filter(pk=self.work.pk).exists())
            self.assertTrue(Attachment.objects.filter(pk=self.attachment.pk).exists())
            self.assertTrue(CaptureOriginal.objects.filter(pk=original.pk).exists())
            self.assertEqual(self.stored_bytes(original.file.name), original_bytes)
            self.assertEqual(
                self.stored_bytes(self.attachment.attachment.name), self.blob
            )
            self.assertEqual(WorkOrderDeletionRecord.objects.count(), 0)
            self.assertEqual(prepared.read_progress(request, impact), progress)
            self.assertEqual(self.private_snapshot(), before)
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)

    def test_direct_owner_reparent_refuses_old_binding_even_after_new_scope_grant(self):
        """A synthetic ORM writer changes identity, not historical/copy authority."""
        self.prepare_operations()
        tenant, other, attachment, content = self.neighbor()
        other_grant = ClientScopeGrant.objects.create(user=self.actor, client=tenant)
        name = self.attachment.attachment.name
        attachment_pk = self.attachment.pk

        def operation():
            prepared, request, impact, _ = self.retained_progress(
                operation='work_document.metadata', source_pk=attachment_pk
            )
            before = self.private_snapshot()
            other_grant.delete()
            # Direct synthetic ORM observation, not an authorized mutation API.
            self.attachment.model_id = other.pk
            self.attachment.save(rebuild=False)
            with patch.object(
                default_storage, 'open', wraps=default_storage.open
            ) as opened:
                self.assertIsNone(
                    documents.read_work_document_content(
                        self.actor,
                        environment='s3-test',
                        attachment_pk=attachment_pk,
                        max_bytes=128,
                    )
                )
            opened.assert_not_called()
            self.refuses_old_progress(prepared, request, impact)
            ClientScopeGrant.objects.create(user=self.actor, client=tenant)
            current = documents.read_work_document(
                self.actor, environment='s3-test', attachment_pk=attachment_pk
            )
            self.assertIsNotNone(current)
            self.assertEqual(current.owner.pk, other.pk)
            # Restore the original client-ID set: policy mismatch must not mask
            # the independent whole-source/typed-owner binding refusal below.
            prepared._source._check_policy()
            fresh = self.prepare(
                operation='work_document.metadata', source_pk=attachment_pk
            )
            self.assertNotEqual(fresh._payload, prepared._source._payload)
            self.refuses_old_progress(prepared, request, impact)
            self.assertEqual(self.private_snapshot(), before)
            self.assertEqual(self.stored_bytes(name), self.blob)
            self.assertEqual(self.stored_bytes(attachment.attachment.name), content)
            self.assertEqual(WorkOrderDeletionRecord.objects.count(), 0)
            return True

        assert_literal_bool(self, self.run_boundary(operation), True)
