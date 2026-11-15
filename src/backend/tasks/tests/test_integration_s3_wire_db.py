"""S3 producer-to-JSON controls; simulated attribution is not source proof."""

import json
import os
from base64 import b64decode
from datetime import date
from hashlib import sha256
from tempfile import TemporaryDirectory
from typing import cast
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from assets.models import AssetMaintenanceRecord, ClientScopeGrant
from assets.tests.integration_attribution_test_support import (
    assume_synthetic_attribution,
)
from common.models import Attachment
from tasks import integration_s3_details as details
from tasks import integration_s3_documents as documents
from tasks import integration_s3_wire as wire
from tasks.models import WorkOrder
from tasks.tests.test_integration_s3_document_metadata_db import DocumentFixture


class WireFixture(DocumentFixture):
    """Invoke the adapter or characterize the real producer/encoder composition."""

    def read_json(self, operation, **kwargs):
        """Keep the pre-adapter fallback real and free of a fake authorization result."""
        reader = getattr(wire, operation, None)
        if reader is not None:
            return reader(self.actor, environment='s3-test', **kwargs)
        readers = {
            'read_work_document_metadata_json': documents.read_work_document,
            'read_machine_document_metadata_json': documents.read_machine_document,
            'read_work_document_content_json': documents.read_work_document_content,
            'read_machine_document_content_json': documents.read_machine_document_content,
            'read_work_details_json': details.read_work_details,
            'read_maintenance_details_json': details.read_maintenance_details,
        }
        record = readers[operation](self.actor, environment='s3-test', **kwargs)
        return wire.encode_source_record(record) if record is not None else None

    def prepare_operations(self):
        """Use section-local real storage and native synthetic maintenance rows."""
        directory = TemporaryDirectory(prefix='s3-wire-', dir=os.environ['TMPDIR'])
        cast(TestCase, self).addCleanup(directory.cleanup)
        settings = override_settings(
            MEDIA_ROOT=directory.name,
            STORAGES={
                'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'}
            },
            AIMMS_ATTACHMENT_CONTENT_MAX_BYTES=128,
        )
        settings.enable()
        cast(TestCase, self).addCleanup(settings.disable)
        self.blob = b'\x00\xffOriginal wire bytes\r\n'
        self.attachment.attachment = SimpleUploadedFile('Synthetic.bin', self.blob)
        self.attachment.save(rebuild=False)
        self.maintenance = AssetMaintenanceRecord.objects.create(
            machine=self.machine,
            date=date(2020, 1, 2),
            summary='Synthetic wire event',
            details='Literal synthetic history',
        )
        attachment = {'attachment_pk': self.attachment.pk}
        content = {**attachment, 'max_bytes': 128}
        return (
            ('read_work_document_metadata_json', attachment, 'workorder'),
            ('read_machine_document_metadata_json', attachment, 'assetmachine'),
            ('read_work_document_content_json', content, 'workorder'),
            ('read_machine_document_content_json', content, 'assetmachine'),
            ('read_work_details_json', {'work_pk': self.work.pk}, None),
            ('read_maintenance_details_json', {'record_pk': self.maintenance.pk}, None),
        )

    def select_owner(self, owner):
        """Use actual typed owners rather than borrowing an equal-numbered key."""
        if owner:
            self.attachment.model_type = owner
            self.attachment.model_id = (
                self.work.pk if owner == 'workorder' else self.machine.pk
            )
            self.attachment.save(rebuild=False)


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
    AIMMS_SINGLE_SITE_CLIENT_CODE='missing-s3-test-client',
)
@assume_synthetic_attribution
class SimulatedSourceWireTests(WireFixture, TestCase):
    """Test actual JSON emission and real current grants after a simulated proof gate."""

    def test_metadata_revocation_during_encoding_withholds_serialized_result(self):
        """An allowed source observation cannot authorize bytes after encoding work."""
        operation = 'read_work_document_metadata_json'
        kwargs = {'attachment_pk': self.attachment.pk}
        allowed = self.read_json(operation, **kwargs)
        self.assertIs(type(allowed), bytes)
        self.assertEqual(
            json.loads(cast(bytes, allowed))['record']['owner']['domain'], 'work'
        )
        original = wire.encode_source_record

        def revoked(record):
            result = original(record)
            self.grant.delete()
            return result

        with patch.object(wire, 'encode_source_record', new=revoked):
            self.assertIsNone(self.read_json(operation, **kwargs))

    def test_all_source_readers_recheck_authority_after_encoding(self):
        """Each public reader needs a final check, including binary/maintenance paths."""
        operations = self.prepare_operations()
        original = wire.encode_source_record
        for operation, kwargs, owner in operations:
            with self.subTest(operation=operation):
                self.select_owner(owner)
                self.assertIs(type(self.read_json(operation, **kwargs)), bytes)
                case = {'grant': self.grant}

                def revoked(record, *, current=case):
                    result = original(record)
                    current['grant'].delete()
                    return result

                try:
                    with patch.object(wire, 'encode_source_record', new=revoked):
                        self.assertIsNone(self.read_json(operation, **kwargs))
                finally:
                    self.grant = ClientScopeGrant.objects.create(
                        user=self.actor, client=self.tenant
                    )
                self.assertIs(type(self.read_json(operation, **kwargs)), bytes)

    def test_codec_none_is_a_fault_not_an_empty_success(self):
        """A valid source plus an invalid encoder result cannot become unavailable."""
        operation = 'read_work_document_metadata_json'
        kwargs = {'attachment_pk': self.attachment.pk}
        self.assertIs(type(self.read_json(operation, **kwargs)), bytes)
        with patch.object(wire, 'encode_source_record', return_value=None):
            with self.assertRaises(wire.SourceSerializationError):
                self.read_json(operation, **kwargs)

    def test_changed_source_during_encoding_withholds_stale_serialization(self):
        """Direct/bulk changes are detected even without a universal revision bump."""
        operations = self.prepare_operations()
        original = wire.encode_source_record
        for operation, kwargs, owner in operations:
            with self.subTest(operation=operation):
                self.select_owner(owner)
                self.assertIs(type(self.read_json(operation, **kwargs)), bytes)
                if owner:
                    row, name = (
                        Attachment.objects.filter(pk=self.attachment.pk),
                        'comment',
                    )
                elif 'work_details' in operation:
                    row, name = WorkOrder.objects.filter(pk=self.work.pk), 'title'
                else:
                    row, name = (
                        AssetMaintenanceRecord.objects.filter(pk=self.maintenance.pk),
                        'details',
                    )
                case = {'row': row, 'name': name, 'value': f'Changed {operation}'}

                def changed(record, *, current=case):
                    result = original(record)
                    current['row'].update(**{current['name']: current['value']})
                    return result

                with patch.object(wire, 'encode_source_record', new=changed):
                    self.assertIsNone(self.read_json(operation, **kwargs))

    def test_encoder_faults_recheck_authority_before_disclosing_failure(self):
        """Source failures stay failures while allowed, and are opaque after revocation."""
        for operation, kwargs, owner in self.prepare_operations():
            with self.subTest(operation=operation):
                self.select_owner(owner)
                self.assertIs(type(self.read_json(operation, **kwargs)), bytes)
                fault = wire.SourceSerializationError(
                    'Synthetic encoder source failure'
                )
                with patch.object(wire, 'encode_source_record', side_effect=fault):
                    with self.assertRaises(wire.SourceSerializationError):
                        self.read_json(operation, **kwargs)
                case = {'grant': self.grant}

                def revoked(record, *, current=case):
                    current['grant'].delete()
                    raise wire.SourceSerializationError(
                        'Synthetic encoder source failure'
                    )

                try:
                    with patch.object(wire, 'encode_source_record', new=revoked):
                        self.assertIsNone(self.read_json(operation, **kwargs))
                finally:
                    self.grant = ClientScopeGrant.objects.create(
                        user=self.actor, client=self.tenant
                    )

    def test_native_bytes_revisions_and_owner_domains_survive_real_encoder(self):
        """Exercise actual producers through JSON and reopen neither file nor link."""
        from common.attachment_revision import attachment_revision

        for operation, kwargs, owner in self.prepare_operations():
            if 'content_json' not in operation:
                continue
            with self.subTest(operation=operation):
                self.select_owner(owner)
                storage = self.attachment.attachment.storage
                with patch.object(storage, 'open', wraps=storage.open) as opened:
                    encoded = self.read_json(operation, **kwargs)
                opened.assert_called_once()
                self.assertIs(type(encoded), bytes)
                body = json.loads(cast(bytes, encoded))['record']
                self.assertEqual(
                    b64decode(body['content']['data'], validate=True), self.blob
                )
                self.assertEqual(
                    body['content']['sha256'], sha256(self.blob).hexdigest()
                )
                self.assertEqual(body['metadata']['source_class'], 'native_attachment')
                self.assertEqual(
                    body['metadata']['external_link'], self.attachment.link
                )
                self.assertEqual(
                    body['metadata']['metadata_revision'],
                    attachment_revision(self.attachment),
                )
                self.assertEqual(
                    body['metadata']['owner']['domain'],
                    'work' if owner == 'workorder' else 'machine',
                )


@override_settings(
    AIMMS_MAINTENANCE_SCOPE_RESOLVER='tasks.scope.granted_client_scope_resolver',
    AIMMS_SINGLE_SITE_CLIENT_CODE='missing-s3-test-client',
)
class RealWithholdingWireTests(WireFixture, TestCase):
    """Keep native visible prerequisites positive without neutralizing any proof gate."""

    def test_unproven_real_records_never_reach_encoder_or_content_storage(self):
        """Native scope and current grants cannot certify historic JSON observations."""
        from tasks.scope import (
            maintenance_record_scope_filter,
            scoped_machines,
            scoped_work_orders,
        )

        operations = self.prepare_operations()
        self.assertTrue(scoped_machines(self.actor).filter(pk=self.machine.pk).exists())
        self.assertTrue(scoped_work_orders(self.actor).filter(pk=self.work.pk).exists())
        self.assertTrue(
            AssetMaintenanceRecord.objects.filter(
                maintenance_record_scope_filter(self.actor), pk=self.maintenance.pk
            ).exists()
        )
        self.assertTrue(ClientScopeGrant.objects.filter(pk=self.grant.pk).exists())
        for operation, kwargs, owner in operations:
            with self.subTest(operation=operation):
                self.select_owner(owner)
                with (
                    patch.object(wire, 'encode_source_record') as encoded,
                    patch('django.core.files.storage.default_storage.open') as opened,
                ):
                    self.assertIsNone(self.read_json(operation, **kwargs))
                encoded.assert_not_called()
                opened.assert_not_called()
