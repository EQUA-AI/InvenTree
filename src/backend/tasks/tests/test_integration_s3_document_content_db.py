"""S3 original-byte binding controls; admission is explicitly simulated."""

import hashlib
import importlib
import io
import os
import tempfile
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from assets.integration_sources import (
    AttachmentContentAbsentError,
    AttachmentContentLimitError,
    AttachmentContentUnavailableError,
)
from assets.tests.integration_attribution_test_support import (
    assume_synthetic_attribution,
)
from tasks.integration_attachments import read_work_attachment_content
from tasks.tests.test_integration_s3_document_metadata_db import DocumentFixture


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
    AIMMS_SINGLE_SITE_CLIENT_CODE='missing-s3-test-client',
)
@assume_synthetic_attribution
class SimulatedDocumentContentTests(DocumentFixture, TestCase):
    """Exercise actual local storage bytes, never real historical authority."""

    def setUp(self):
        """Use per-test disposable filesystem storage and synthetic native owners."""
        super().setUp()
        directory = self.enterContext(
            tempfile.TemporaryDirectory(dir=os.environ['TMPDIR'])
        )
        self.enterContext(override_settings(MEDIA_ROOT=directory))
        self.content = b'\x00original S3 bytes\xff\n'

    def stored_file(self):
        """Persist a native synthetic file without unrelated thumbnail offload."""
        self.attachment.attachment = SimpleUploadedFile('source.bin', self.content)
        self.attachment.save(rebuild=False)
        return self.attachment

    def read_content(self, **kwargs):
        """Characterize the old byte seam until the metadata-bound operation exists."""
        module = importlib.import_module('tasks.integration_s3_documents')
        reader = getattr(
            module, 'read_work_document_content', read_work_attachment_content
        )
        return reader(
            self.actor,
            environment='s3-test',
            attachment_pk=self.attachment.pk,
            max_bytes=kwargs.pop('max_bytes', 128),
            **kwargs,
        )

    def test_original_bytes_are_bound_to_current_authorized_metadata(self):
        """A byte response must identify the exact metadata/owner observation."""
        self.stored_file()
        result = self.read_content()
        self.assertIsNotNone(result)
        self.assertEqual(result.content, self.content)
        self.assertEqual(
            result.content_sha256, hashlib.sha256(self.content).hexdigest()
        )
        self.assertIsNotNone(getattr(result, 'metadata', None))
        self.assertEqual(result.metadata.identity.pk, self.attachment.pk)
        self.assertEqual(result.metadata.owner.pk, self.work.pk)
        self.assertEqual(result.metadata.kind, 'file_and_link')
        self.assertEqual(result.metadata.external_link, self.attachment.link)
        self.assertNotIn(self.content.decode('latin1'), repr(result))

    def test_link_only_has_absent_content_without_open_or_fetch(self):
        """An external link is not an empty stored file or an instruction to fetch."""
        with patch('django.core.files.storage.default_storage.open') as opened:
            with self.assertRaises(AttachmentContentAbsentError):
                self.read_content()
        opened.assert_not_called()

    def test_machine_original_bytes_have_typed_owner_and_metadata_binding(self):
        """Machine bytes need the same full binding, not a Work-ID substitution."""
        from assets.integration_contracts import MachineSourceIdentity
        from assets.integration_sources import read_machine_attachment_content

        self.stored_file()
        self.attachment.model_type = 'assetmachine'
        self.attachment.model_id = self.machine.pk
        self.attachment.save(rebuild=False)
        module = importlib.import_module('tasks.integration_s3_documents')
        reader = getattr(
            module, 'read_machine_document_content', read_machine_attachment_content
        )
        result = reader(
            self.actor,
            environment='s3-test',
            attachment_pk=self.attachment.pk,
            max_bytes=128,
        )
        self.assertIsNotNone(result)
        self.assertEqual(result.content, self.content)
        self.assertIsNotNone(getattr(result, 'metadata', None))
        self.assertIs(type(result.metadata.owner), MachineSourceIdentity)

    def test_short_reads_preserve_complete_original_and_close_handle(self):
        """A short non-EOF storage read is not a complete file."""
        self.stored_file()

        class ShortReads(io.BytesIO):
            def read(self, size=-1):
                return super().read(min(size, 2))

        stream = ShortReads(self.content)
        with patch(
            'django.core.files.storage.default_storage.open', return_value=stream
        ):
            result = self.read_content()
        self.assertEqual(result.content, self.content)
        self.assertTrue(stream.closed)

    def test_byte_limits_and_empty_stored_file_are_not_link_semantics(self):
        """Exact-cap, over-cap and a real zero-byte file retain their distinctions."""
        self.stored_file()
        result = self.read_content(max_bytes=len(self.content))
        self.assertEqual(result.content, self.content)
        with self.assertRaises(AttachmentContentLimitError):
            self.read_content(max_bytes=len(self.content) - 1)
        from django.core.files.storage import default_storage

        with default_storage.open(self.attachment.attachment.name, 'wb'):
            pass
        empty = self.read_content()
        self.assertEqual(empty.content, b'')
        self.assertEqual(empty.content_sha256, hashlib.sha256(b'').hexdigest())

    def test_changed_same_reference_bytes_have_distinct_content_digest(self):
        """The metadata CAS cannot assert immutable or unchanged file content."""
        from django.core.files.storage import default_storage

        self.stored_file()
        first = self.read_content()
        changed = b'x' * len(self.content)
        with default_storage.open(self.attachment.attachment.name, 'wb') as stream:
            stream.write(changed)
        second = self.read_content()
        self.assertEqual(
            first.metadata.metadata_revision, second.metadata.metadata_revision
        )
        self.assertNotEqual(first.content_sha256, second.content_sha256)
        self.assertEqual(second.content, changed)

    def test_metadata_change_during_read_refuses_stale_combination(self):
        """Authorized bytes must not be paired with a different link/comment epoch."""
        self.stored_file()
        expected = self.content
        attachment_pk = self.attachment.pk

        class ChangedMetadata(io.BytesIO):
            def read(self, size=-1):
                from common.models import Attachment

                Attachment.objects.filter(pk=attachment_pk).update(comment='Changed')
                return super().read(size)

        stream = ChangedMetadata(expected)
        with patch(
            'django.core.files.storage.default_storage.open', return_value=stream
        ):
            self.assertIsNone(self.read_content())
        self.assertTrue(stream.closed)

    def test_storage_failure_after_metadata_change_does_not_return_stale_file_state(
        self,
    ):
        """Failure carriers must be rechecked against the bound metadata as well."""
        from common.models import Attachment

        self.stored_file()

        def changed(*args, **kwargs):
            Attachment.objects.filter(pk=self.attachment.pk).update(comment='Changed')
            raise OSError('Synthetic storage detail')

        with patch(
            'django.core.files.storage.default_storage.open', side_effect=changed
        ):
            try:
                result = self.read_content()
            except AttachmentContentUnavailableError:
                result = 'stale file-state error'
        self.assertIsNone(result)

    def test_revocation_during_final_content_projection_withholds_bytes(self):
        """The final byte-envelope constructor must precede the last authority check."""
        from tasks import integration_s3_documents as documents

        self.stored_file()
        self.assertIsNotNone(self.read_content())
        original = documents.DocumentContentSourceRecord.__post_init__

        def revoked(record):
            original(record)
            self.grant.delete()

        with patch.object(
            documents.DocumentContentSourceRecord, '__post_init__', new=revoked
        ):
            self.assertIsNone(self.read_content())

    def test_machine_revocation_during_final_projection_withholds_bytes(self):
        """The same final-emission boundary protects typed machine documents."""
        from tasks import integration_s3_documents as documents

        self.stored_file()
        self.attachment.model_type = 'assetmachine'
        self.attachment.model_id = self.machine.pk
        self.attachment.save(rebuild=False)
        kwargs = {
            'environment': 's3-test',
            'attachment_pk': self.attachment.pk,
            'max_bytes': 128,
        }
        self.assertIsNotNone(
            documents.read_machine_document_content(self.actor, **kwargs)
        )
        original = documents.DocumentContentSourceRecord.__post_init__

        def revoked(record):
            original(record)
            self.grant.delete()

        with patch.object(
            documents.DocumentContentSourceRecord, '__post_init__', new=revoked
        ):
            self.assertIsNone(
                documents.read_machine_document_content(self.actor, **kwargs)
            )

    def test_metadata_change_during_final_projection_withholds_bytes(self):
        """A constructed result cannot retain a stale comment/link observation."""
        from common.models import Attachment
        from tasks import integration_s3_documents as documents

        self.stored_file()
        self.assertIsNotNone(self.read_content())
        original = documents.DocumentContentSourceRecord.__post_init__

        def changed(record):
            original(record)
            Attachment.objects.filter(pk=self.attachment.pk).update(comment='Changed')

        with patch.object(
            documents.DocumentContentSourceRecord, '__post_init__', new=changed
        ):
            self.assertIsNone(self.read_content())

    def test_authorized_final_validation_fault_is_generic_unavailable(self):
        """Invalid final content is a source fault, not empty data or raw detail."""
        from tasks import integration_s3_documents as documents

        self.stored_file()
        self.assertIsNotNone(self.read_content())

        def invalid(record):
            raise ValueError('Private synthetic validation detail')

        with patch.object(
            documents.DocumentContentSourceRecord, '__post_init__', new=invalid
        ):
            with self.assertRaises(AttachmentContentUnavailableError) as raised:
                self.read_content()
        self.assertNotIn('Private synthetic', str(raised.exception))

    def test_final_validation_fault_after_revocation_is_withheld(self):
        """A failed final constructor must recheck authority before disclosing a fault."""
        from tasks import integration_s3_documents as documents

        self.stored_file()
        self.assertIsNotNone(self.read_content())

        def revoked(record):
            self.grant.delete()
            raise ValueError('Private synthetic validation detail')

        with patch.object(
            documents.DocumentContentSourceRecord, '__post_init__', new=revoked
        ):
            try:
                self.assertIsNone(self.read_content())
            except ValueError:
                self.fail('Final validation fault escaped after authority loss')
