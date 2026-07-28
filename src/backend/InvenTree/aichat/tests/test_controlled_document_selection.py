"""Scope and asset authorization tests for controlled document selection."""

from types import SimpleNamespace

from django.test import TestCase

from aichat.models import ControlledDocument, ControlledDocumentState
from aichat.services.controlled_document_selection import (
    ControlledDocumentUnavailable,
    resolve_selected_document,
)


class ControlledDocumentSelectionTests(TestCase):
    """A document UUID narrows only after scope and asset checks succeed."""

    def document_values(self, **overrides):
        """Build one current indexed Pump Station source revision."""
        values = {
            'document_id': 'aimms-tc-inf-ps1-manual',
            'revision': '2.0',
            'title': 'Influent Pump Station No. 1 Technical Manual',
            'document_class': 'technical_manual',
            'scope_key': 'epcon-experimental',
            'scope_hash': 'a' * 64,
            'access_class': 'maintenance_authorized',
            'source_filename': 'pump-station-manual.md',
            'source_location': '/controlled/pump-station-manual.md',
            'source_sha256': 'b' * 64,
            'asset_id': 'TC-INF-PS1-001',
            'work_order_id': 'WO-WW-R-001',
            'state': ControlledDocumentState.INDEXED,
            'is_current': True,
            'search_index_name': 'eaits-manuals-v4a',
        }
        values.update(overrides)
        return values

    @staticmethod
    def machine_record(serial='TC-INF-PS1-001'):
        """Return the only asset coordinate a selected document may match."""
        return SimpleNamespace(serial=serial)

    def test_selection_requires_current_document_scope_and_asset_match(self):
        """An opaque ID cannot select a document across scope or asset boundaries."""
        document = ControlledDocument.objects.create(**self.document_values())

        selected = resolve_selected_document(
            selection_id=str(document.selection_id),
            scope_key='epcon-experimental',
            scope_hash='a' * 64,
            record=self.machine_record(),
        )

        self.assertEqual(selected.document.pk, document.pk)
        self.assertEqual(selected.payload()['revision'], '2.0')

        with self.assertRaises(ControlledDocumentUnavailable):
            resolve_selected_document(
                selection_id=str(document.selection_id),
                scope_key='other-scope',
                scope_hash='c' * 64,
                record=self.machine_record(),
            )
        with self.assertRaises(ControlledDocumentUnavailable):
            resolve_selected_document(
                selection_id=str(document.selection_id),
                scope_key='epcon-experimental',
                scope_hash='a' * 64,
                record=self.machine_record('TC-INF-PS1-999'),
            )

    def test_work_order_context_requires_matching_governed_work_order(self):
        """A Pump Station document cannot be silently reused for another work order."""
        document = ControlledDocument.objects.create(**self.document_values())
        work_order = SimpleNamespace(
            reference='WO-UNRELATED-001', machine=self.machine_record()
        )

        with self.assertRaises(ControlledDocumentUnavailable):
            resolve_selected_document(
                selection_id=str(document.selection_id),
                scope_key='epcon-experimental',
                scope_hash='a' * 64,
                record=work_order,
            )
