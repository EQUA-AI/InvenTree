"""S3 document metadata controls; positives explicitly simulate attribution."""

import importlib
import importlib.util
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, override_settings

from assets.models import AssetMachine, Client, ClientScopeGrant
from assets.tests.integration_attribution_test_support import (
    assume_synthetic_attribution,
)
from common.models import Attachment
from company.models import Company
from tasks.integration_attachments import read_work_attachment
from tasks.models import WorkOrder
from users.models import RuleSet


class DocumentFixture:
    """Build synthetic records without file IO or background attachment tasks."""

    def setUp(self):
        """Create native Work view authority and a current explicit grant."""
        self.actor = get_user_model().objects.create_user(username='s3-doc-reader')
        self.group = Group.objects.create(name='S3 document readers')
        self.actor.groups.add(self.group)
        self.rule, _ = RuleSet.objects.update_or_create(
            group=self.group, name='work_order', defaults={'can_view': True}
        )
        self.tenant = Client.objects.create(name='Synthetic S3 A', code='s3-doc-a')
        self.grant = ClientScopeGrant.objects.create(
            user=self.actor, client=self.tenant
        )
        self.machine = AssetMachine.objects.create(
            name='Synthetic S3', client=self.tenant
        )
        self.work = WorkOrder.objects.create(
            title='Synthetic S3 Work',
            status=WorkOrder.STATUS_BACKLOG,
            priority=WorkOrder.PRIORITY_MEDIUM,
            machine=self.machine,
        )
        self.attachment = Attachment(
            model_type='workorder',
            model_id=self.work.pk,
            link='https://example.invalid/reference?literal=%2F',
            comment='Synthetic note',
        )
        self.attachment.save(rebuild=False)

    def read_work_document(self):
        """Use the new operation or characterize the existing identity-only seam."""
        name = 'tasks.integration_s3_documents'
        reader = (
            importlib.import_module(name).read_work_document
            if importlib.util.find_spec(name) is not None
            else read_work_attachment
        )
        return reader(
            self.actor, environment='s3-test', attachment_pk=self.attachment.pk
        )


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
    AIMMS_SINGLE_SITE_CLIENT_CODE='missing-s3-test-client',
)
@assume_synthetic_attribution
class SimulatedDocumentMetadataTests(DocumentFixture, TestCase):
    """Test downstream metadata, not genuine historical admission."""

    def test_link_only_metadata_preserves_external_link_without_opening_storage(self):
        """The source operation must preserve the actual link, not fake file bytes."""
        with patch('django.core.files.storage.default_storage.open') as opened:
            result = self.read_work_document()
        self.assertIsNotNone(result)
        self.assertEqual(getattr(result, 'kind', None), 'link')
        self.assertEqual(result.external_link, self.attachment.link)
        self.assertIsNone(result.filename)
        self.assertEqual(result.comment, 'Synthetic note')
        opened.assert_not_called()

    def test_existing_work_attachment_identity_remains_minimal(self):
        """The new operation must not alter the old literal schema/shape."""
        result = read_work_attachment(
            self.actor, environment='s3-test', attachment_pk=self.attachment.pk
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.schema_version, 'aimms.work-attachment.basic.v1')
        self.assertFalse(hasattr(result, 'external_link'))

    def test_dual_valued_metadata_preserves_both_facts_without_storage_coordinates(
        self,
    ):
        """A stored file reference must not discard an authorized external link."""
        from dataclasses import asdict

        from common.attachment_revision import attachment_revision

        Attachment.objects.filter(pk=self.attachment.pk).update(
            attachment='private/subdir/synthetic.bin', file_size=37, is_image=False
        )
        with patch('django.core.files.storage.default_storage.open') as opened:
            result = self.read_work_document()
        self.assertEqual(result.kind, 'file_and_link')
        self.assertEqual(result.filename, 'synthetic.bin')
        self.assertEqual(result.external_link, self.attachment.link)
        self.assertEqual(result.file_size, 37)
        self.attachment.refresh_from_db()
        self.assertEqual(result.metadata_revision, attachment_revision(self.attachment))
        self.assertNotIn('private/subdir', repr(asdict(result)))
        self.assertFalse(hasattr(result, 'thumbnail'))
        self.assertFalse(hasattr(result, 'upload_user'))
        opened.assert_not_called()

    def test_machine_metadata_uses_typed_machine_owner(self):
        """An equal-numbered Work or other model cannot stand in for the owner."""
        from assets.integration_contracts import MachineSourceIdentity
        from assets.integration_sources import read_machine_attachment

        self.attachment.model_type = 'assetmachine'
        self.attachment.model_id = self.machine.pk
        self.attachment.save(rebuild=False)
        module = importlib.import_module('tasks.integration_s3_documents')
        reader = getattr(module, 'read_machine_document', read_machine_attachment)
        result = reader(
            self.actor, environment='s3-test', attachment_pk=self.attachment.pk
        )
        self.assertEqual(getattr(result, 'kind', None), 'link')
        self.assertIs(type(result.owner), MachineSourceIdentity)
        self.assertEqual(result.owner.pk, self.machine.pk)
        self.assertIsNone(self.read_work_document())

    def test_metadata_change_during_projection_withholds_old_observation(self):
        """Projection work cannot turn a changed link into a stale returned result."""
        from tasks import integration_s3_documents as documents

        original = documents._project_metadata

        def changed(*args):
            result = original(*args)
            Attachment.objects.filter(pk=self.attachment.pk).update(
                link='https://example.invalid/changed'
            )
            return result

        self.assertIsNotNone(self.read_work_document())
        with patch.object(documents, '_project_metadata', side_effect=changed):
            self.assertIsNone(self.read_work_document())

    def test_grant_revocation_during_projection_withholds_result(self):
        """An earlier successful identity check is not a reusable grant."""
        from tasks import integration_s3_documents as documents

        original = documents._project_metadata

        def revoked(*args):
            result = original(*args)
            self.grant.delete()
            return result

        self.assertIsNotNone(self.read_work_document())
        with patch.object(documents, '_project_metadata', side_effect=revoked):
            self.assertIsNone(self.read_work_document())

    def test_uploaded_source_class_cannot_be_mistaken_for_a_controlled_manual(self):
        """The envelope must preserve the native attachment source category."""
        result = self.read_work_document()
        self.assertEqual(getattr(result, 'source_class', None), 'native_attachment')

    def test_non_posix_storage_reference_is_not_exposed_as_a_filename(self):
        """Do not turn an unsupported storage path into public filename metadata."""
        from tasks.integration_s3_documents import DocumentMetadataUnavailableError

        Attachment.objects.filter(pk=self.attachment.pk).update(
            attachment=r'private\subdir\synthetic.bin'
        )
        with self.assertRaises(DocumentMetadataUnavailableError):
            self.read_work_document()

    def test_invalid_metadata_is_not_a_successful_empty_document(self):
        """Invalid native rows are unavailable, not a synthetic file or null link."""
        from tasks.integration_s3_documents import DocumentMetadataUnavailableError

        self.assertIsNotNone(self.read_work_document())
        Attachment.objects.filter(pk=self.attachment.pk).update(
            attachment=None, link=None
        )
        with self.assertRaises(DocumentMetadataUnavailableError):
            self.read_work_document()

    def test_invalid_metadata_error_is_withheld_after_grant_revocation(self):
        """A source-validation error must not expose state after authority is lost."""
        from tasks import integration_s3_documents as documents

        self.assertIsNotNone(self.read_work_document())
        Attachment.objects.filter(pk=self.attachment.pk).update(
            attachment=None, link=None
        )
        original = documents._project_metadata

        def revoked(*args):
            self.grant.delete()
            return original(*args)

        with patch.object(documents, '_project_metadata', side_effect=revoked):
            try:
                result = self.read_work_document()
            except documents.DocumentMetadataUnavailableError:
                result = 'unauthorized metadata error'
        self.assertIsNone(result)


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
    AIMMS_SINGLE_SITE_CLIENT_CODE='missing-s3-test-client',
)
class ActualDocumentWithholdingTests(DocumentFixture, TestCase):
    """Exercise the real deny policy, without the attribution simulation."""

    def test_unproven_document_metadata_and_content_are_not_read(self):
        """Positive native authority must not substitute for historical proof."""
        from tasks.integration_s3_documents import read_work_document_content
        from tasks.scope import scoped_work_orders

        self.assertTrue(scoped_work_orders(self.actor).filter(pk=self.work.pk).exists())
        self.assertTrue(WorkOrder.check_related_permission('view', self.actor))
        with patch('django.core.files.storage.default_storage.open') as opened:
            self.assertIsNone(self.read_work_document())
            self.assertIsNone(
                read_work_document_content(
                    self.actor,
                    environment='s3-test',
                    attachment_pk=self.attachment.pk,
                    max_bytes=128,
                )
            )
        opened.assert_not_called()

    def test_unproven_machine_document_content_and_metadata_are_not_read(self):
        """Current machine visibility is not historical attachment proof."""
        from tasks.integration_s3_documents import (
            read_machine_document,
            read_machine_document_content,
        )
        from tasks.scope import scoped_machines

        self.assertTrue(scoped_machines(self.actor).filter(pk=self.machine.pk).exists())
        Attachment.objects.filter(pk=self.attachment.pk).update(
            model_type='assetmachine',
            model_id=self.machine.pk,
            attachment='private/synthetic.bin',
        )
        with patch('django.core.files.storage.default_storage.open') as opened:
            self.assertIsNone(
                read_machine_document(
                    self.actor, environment='s3-test', attachment_pk=self.attachment.pk
                )
            )
            self.assertIsNone(
                read_machine_document_content(
                    self.actor,
                    environment='s3-test',
                    attachment_pk=self.attachment.pk,
                    max_bytes=128,
                )
            )
        opened.assert_not_called()

    def test_shared_machine_less_work_remains_native_visible_and_withheld(self):
        """A sales customer or shared native visibility is not client attribution."""
        from tasks.scope import scoped_work_orders

        self.work.customer = Company.objects.create(name='Synthetic S3 customer')
        self.work.machine = None
        self.work.save(update_fields=['customer', 'machine'])
        self.assertTrue(scoped_work_orders(self.actor).filter(pk=self.work.pk).exists())
        self.assertIsNone(self.read_work_document())
