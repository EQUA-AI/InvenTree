"""Database invariants for controlled document registry records."""

from django.db import IntegrityError, transaction
from django.test import TestCase

from aichat.models import ControlledDocument, ControlledDocumentState
from aichat.services import controlled_documents


class ControlledDocumentModelTests(TestCase):
    """Prove registry rows cannot become ambiguous retrieval sources."""

    def document_values(self, **overrides):
        """Return valid values for an indexed controlled document revision."""
        values = {
            'document_id': 'aimms-tc-inf-ps1-manual',
            'revision': '2.0',
            'title': 'Influent Pump Station No. 1 Technical Manual',
            'document_class': 'technical_manual',
            'scope_key': 'epcon-experimental',
            'scope_hash': 'a' * 64,
            'access_class': 'maintenance_authorized',
            'source_filename': 'pump-station-manual.md',
            'source_location': '/home/inventree/data/media/ai/controlled-documents/pump-station-manual.md',
            'source_sha256': 'b' * 64,
            'asset_id': 'TC-INF-PS1-001',
            'state': ControlledDocumentState.INDEXED,
            'is_current': True,
            'search_index_name': 'eaits-manuals-v4a',
        }
        values.update(overrides)
        return values

    def test_scope_document_revision_is_unique(self):
        """A source revision has one registry identity within its scope."""
        document = ControlledDocument.objects.create(**self.document_values())
        self.assertIsNotNone(document.selection_id)

        with self.assertRaises(IntegrityError), transaction.atomic():
            ControlledDocument.objects.create(**self.document_values(is_current=False))

    def test_scope_document_has_only_one_current_revision(self):
        """Exact retrieval has at most one default revision per scope."""
        ControlledDocument.objects.create(**self.document_values())

        with self.assertRaises(IntegrityError), transaction.atomic():
            ControlledDocument.objects.create(
                **self.document_values(revision='2.1', source_sha256='c' * 64)
            )

    def test_current_revision_must_be_indexed(self):
        """An unindexed revision cannot be selected for retrieval."""
        with self.assertRaises(IntegrityError), transaction.atomic():
            ControlledDocument.objects.create(
                **self.document_values(
                    state=ControlledDocumentState.DRAFT,
                    is_current=True,
                    search_index_name='',
                    source_sha256='',
                )
            )

    def test_indexed_revision_requires_source_fingerprint_and_index(self):
        """Indexed state always records immutable source and index coordinates."""
        with self.assertRaises(IntegrityError), transaction.atomic():
            ControlledDocument.objects.create(**self.document_values(source_sha256=''))

        with self.assertRaises(IntegrityError), transaction.atomic():
            ControlledDocument.objects.create(
                **self.document_values(search_index_name='')
            )


class ControlledDocumentServiceTests(TestCase):
    """Prove lifecycle operations retain a trusted source and scope boundary."""

    def register_values(self, **overrides):
        """Return required values for a new controlled document revision."""
        values = {
            'document_id': 'aimms-tc-inf-ps1-manual',
            'revision': '2.0',
            'title': 'Influent Pump Station No. 1 Technical Manual',
            'document_class': 'technical_manual',
            'scope_key': 'epcon-experimental',
            'scope_hash': 'a' * 64,
            'access_class': 'maintenance_authorized',
            'source_filename': 'pump-station-manual.md',
            'source_location': '/home/inventree/data/media/ai/controlled-documents/pump-station-manual.md',
            'source_sha256': 'b' * 64,
            'asset_id': 'TC-INF-PS1-001',
        }
        values.update(overrides)
        return values

    def register(self, **overrides):
        """Register one source revision through the production service."""
        return controlled_documents.register_document(
            **self.register_values(**overrides)
        )

    def test_register_rejects_non_sha256_source_fingerprint(self):
        """A document cannot enter the registry without exact source bytes."""
        with self.assertRaises(controlled_documents.ControlledDocumentError):
            self.register(source_sha256='not-a-sha256')

    def test_register_same_source_revision_is_idempotent(self):
        """A retry with identical source bytes reuses the durable registry row."""
        first = self.register()
        second = self.register()

        self.assertEqual(first.pk, second.pk)

        with self.assertRaises(controlled_documents.ControlledDocumentSourceMismatch):
            self.register(source_sha256='c' * 64)

    def test_publishing_new_revision_supersedes_previous_current_revision(self):
        """One document selection cannot resolve two current revisions."""
        self.register()
        controlled_documents.start_indexing(
            scope_key='epcon-experimental',
            scope_hash='a' * 64,
            document_id='aimms-tc-inf-ps1-manual',
            revision='2.0',
        )
        first = controlled_documents.mark_indexed(
            scope_key='epcon-experimental',
            scope_hash='a' * 64,
            document_id='aimms-tc-inf-ps1-manual',
            revision='2.0',
            source_sha256='b' * 64,
            search_index_name='eaits-manuals-v4a',
        )
        self.register(revision='2.1', source_sha256='c' * 64)
        controlled_documents.start_indexing(
            scope_key='epcon-experimental',
            scope_hash='a' * 64,
            document_id='aimms-tc-inf-ps1-manual',
            revision='2.1',
        )
        second = controlled_documents.mark_indexed(
            scope_key='epcon-experimental',
            scope_hash='a' * 64,
            document_id='aimms-tc-inf-ps1-manual',
            revision='2.1',
            source_sha256='c' * 64,
            search_index_name='eaits-manuals-v4a',
        )

        first.refresh_from_db()
        self.assertEqual(first.state, ControlledDocumentState.SUPERSEDED)
        self.assertFalse(first.is_current)
        self.assertEqual(second.state, ControlledDocumentState.INDEXED)
        self.assertTrue(second.is_current)

    def test_publish_rejects_changed_source_and_out_of_scope_reads(self):
        """Indexing cannot publish changed bytes or disclose another scope's row."""
        self.register()
        controlled_documents.start_indexing(
            scope_key='epcon-experimental',
            scope_hash='a' * 64,
            document_id='aimms-tc-inf-ps1-manual',
            revision='2.0',
        )

        with self.assertRaises(controlled_documents.ControlledDocumentSourceMismatch):
            controlled_documents.mark_indexed(
                scope_key='epcon-experimental',
                scope_hash='a' * 64,
                document_id='aimms-tc-inf-ps1-manual',
                revision='2.0',
                source_sha256='c' * 64,
                search_index_name='eaits-manuals-v4a',
            )

        with self.assertRaises(controlled_documents.ControlledDocumentNotFound):
            controlled_documents.get_indexed_document(
                scope_key='other-scope',
                scope_hash='d' * 64,
                document_id='aimms-tc-inf-ps1-manual',
                revision='2.0',
            )
