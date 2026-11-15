"""Native original bytes stay bound through the final private constructor."""

import hashlib
from unittest.mock import patch

from django.core.files.storage import default_storage
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from tasks import integration_attachments

from assets import integration_sources
from assets.integration_sources import AttachmentContentUnavailableError
from assets.models import ClientScopeGrant
from common.models import Attachment
from integration_state.tests.test_native_authority import NativeAuthorityFixture


class NativeByteProjectionTests(NativeAuthorityFixture, TestCase):
    """Both direct owners use actual native grants/permissions and future proof."""

    def setUp(self):
        """Create separately approved actual Work and machine original bytes."""
        super().setUp()
        self.approve(self.attest())
        self.sources = []
        for kind, pk, module, record in (
            (
                'workorder',
                self.new_work.pk,
                integration_attachments,
                integration_attachments.WorkAttachmentContentRecord,
            ),
            (
                'assetmachine',
                self.machine.pk,
                integration_sources,
                integration_sources.MachineAttachmentContentRecord,
            ),
        ):
            source = Attachment(
                model_type=kind,
                model_id=pk,
                attachment=SimpleUploadedFile('native-byte.bin', b'Exact source bytes'),
            )
            source.save(rebuild=False)
            self.addCleanup(source.attachment.delete, save=False)
            self.approve(self.attest('attachment', source.pk))
            reader = (
                module.read_work_attachment_content
                if kind == 'workorder'
                else module.read_machine_attachment_content
            )
            self.sources.append((source, record, reader))

    def read(self, source, reader):
        """Read only the actual run-owned original at its current native owner."""
        return reader(
            self.owner,
            environment=self.namespace.environment,
            attachment_pk=source.pk,
            max_bytes=64,
        )

    def test_coherent_private_constructor_substitution_never_replaces_source_bytes(
        self,
    ):
        """A valid digest for invented bytes is still not the observed original."""
        for source, record, reader in self.sources:
            self.assertEqual(self.read(source, reader).content, b'Exact source bytes')
            actual_init = record.__init__
            for fault in ('bytes', 'identity', 'schema', 'alias'):
                with self.subTest(owner=source.model_type, fault=fault):

                    def construct(
                        value,
                        *args,
                        fault=fault,
                        actual_init=actual_init,
                        source=source,
                        **kwargs,
                    ):
                        actual_init(value, *args, **kwargs)
                        if fault == 'bytes':
                            object.__setattr__(value, 'content', b'Invented bytes')
                            object.__setattr__(
                                value,
                                'content_sha256',
                                hashlib.sha256(b'Invented bytes').hexdigest(),
                            )
                        elif fault == 'identity':
                            object.__setattr__(value.identity, 'pk', source.pk + 1)
                        elif fault == 'schema':
                            object.__setattr__(value, 'schema_version', 'unknown')
                        else:
                            object.__setattr__(value.identity, 'pk', True)

                    with (
                        patch.object(record, '__init__', construct),
                        self.assertRaises(AttachmentContentUnavailableError),
                    ):
                        self.read(source, reader)
            self.assertEqual(self.read(source, reader).content, b'Exact source bytes')

    def test_grant_revocation_inside_private_constructor_withholds_both_owners(self):
        """Bytes captured earlier are never returned after the final grant loss."""
        for source, record, reader in self.sources:
            with self.subTest(owner=source.model_type):
                actual_init = record.__init__
                actual_open = default_storage.open
                revoked, later_opens = [], []

                def construct(
                    value, *args, actual_init=actual_init, revoked=revoked, **kwargs
                ):
                    actual_init(value, *args, **kwargs)
                    ClientScopeGrant.objects.filter(
                        user=self.owner, client=self.client_a
                    ).delete()
                    revoked.append(True)

                def open_original(
                    *args,
                    actual_open=actual_open,
                    revoked=revoked,
                    later_opens=later_opens,
                    **kwargs,
                ):
                    if revoked:
                        later_opens.append(True)
                    return actual_open(*args, **kwargs)

                with (
                    patch.object(record, '__init__', construct),
                    patch.object(default_storage, 'open', side_effect=open_original),
                ):
                    self.assertIsNone(self.read(source, reader))
                self.assertEqual(revoked, [True])
                self.assertEqual(later_opens, [])
                self.owner_grant = ClientScopeGrant.objects.create(
                    user=self.owner, client=self.client_a
                )

    def test_constructor_failure_is_opaque_and_process_failure_is_preserved(self):
        """A private fault has no successful bytes and no raw implementation text."""
        for source, record, reader in self.sources:
            with self.subTest(owner=source.model_type):
                with (
                    patch.object(
                        record,
                        '__init__',
                        side_effect=ValueError('Synthetic private field detail'),
                    ),
                    self.assertRaises(AttachmentContentUnavailableError) as caught,
                ):
                    self.read(source, reader)
                self.assertNotIn('private field detail', str(caught.exception))
                with (
                    patch.object(record, '__init__', side_effect=MemoryError),
                    self.assertRaises(MemoryError),
                ):
                    self.read(source, reader)

    def test_private_assembly_cannot_be_reused_or_copied_after_return(self):
        """One synchronous read never becomes a serialized or later capability."""
        import copy
        import pickle

        from assets.integration_content_read import _original_assembly

        source, _, _ = self.sources[0]
        with _original_assembly(
            self.owner,
            environment=self.namespace.environment,
            attachment_pk=source.pk,
            max_bytes=64,
            owner_reader=integration_attachments.read_work_attachment,
            model_type='workorder',
            owner_field='work',
        ) as assembly:
            self.assertEqual(assembly.read_bytes(), b'Exact source bytes')
            with self.assertRaises(AttachmentContentUnavailableError):
                assembly.read_bytes()
        with patch.object(default_storage, 'open') as opened:
            with self.assertRaises(AttachmentContentUnavailableError):
                assembly.read_bytes()
            for operation in (copy.copy, copy.deepcopy, pickle.dumps):
                with (
                    self.subTest(operation=operation.__name__),
                    self.assertRaises(TypeError),
                ):
                    operation(assembly)
        opened.assert_not_called()

    def test_document_projection_cannot_substitute_native_metadata(self):
        """Metadata identity/comment/type remain bound to independently read rows."""
        from tasks import integration_s3_documents as documents

        project = documents._project_metadata
        for source, _, _ in self.sources:
            reader = (
                documents.read_work_document_content
                if source.model_type == 'workorder'
                else documents.read_machine_document_content
            )
            for fault in ('identity', 'owner', 'comment', 'alias'):
                with self.subTest(owner=source.model_type, fault=fault):

                    def substitute(*args, fault=fault, **kwargs):
                        value = project(*args, **kwargs)
                        if fault == 'identity':
                            object.__setattr__(
                                value.identity, 'pk', value.identity.pk + 1
                            )
                        elif fault == 'owner':
                            object.__setattr__(value.owner, 'pk', value.owner.pk + 1)
                        elif fault == 'comment':
                            object.__setattr__(
                                value, 'comment', 'Invented source comment'
                            )
                        else:
                            object.__setattr__(value, 'file_size', True)
                        return value

                    with patch.object(
                        documents, '_project_metadata', side_effect=substitute
                    ):
                        self.assertIsNone(
                            reader(
                                self.owner,
                                environment=self.namespace.environment,
                                attachment_pk=source.pk,
                                max_bytes=64,
                            )
                        )
            self.assertEqual(
                reader(
                    self.owner,
                    environment=self.namespace.environment,
                    attachment_pk=source.pk,
                    max_bytes=64,
                ).content,
                b'Exact source bytes',
            )
