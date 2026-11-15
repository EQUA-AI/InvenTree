"""S3 pure wire projections; serialized observations are not access grants."""

from integration_state.tests.assertions import assert_literal_bool

import importlib
import importlib.util
import json
from base64 import b64decode
from dataclasses import asdict, replace
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from unittest import TestCase

from assets.integration_contracts import (
    AttachmentSourceIdentity,
    MachineSourceIdentity,
    MaintenanceSourceIdentity,
)
from tasks.integration_s3_details import (
    MaintenanceDetailsSourceRecord,
    WorkDetailsSourceRecord,
)
from tasks.integration_s3_documents import (
    DocumentContentSourceRecord,
    DocumentMetadataSourceRecord,
)
from tasks.integration_sources import WorkSourceIdentity


class SourceWireTests(TestCase):
    """Exercise real envelope-to-JSON encoding, not an AI/HTTP consumer."""

    def metadata(self):
        """Keep literal source labels and equal-numbered cross-domain identities."""
        environment = ' Synthetic/站 A '
        return DocumentMetadataSourceRecord(
            identity=AttachmentSourceIdentity(environment=environment, pk=7),
            owner=WorkSourceIdentity(environment=environment, pk=7),
            kind='link',
            filename=None,
            external_link='https://example.invalid/a?literal=%2F',
            comment='Untrusted synthetic note <script>',
            upload_date=date(2020, 1, 2),
            file_size=0,
            is_image=False,
            metadata_revision='a' * 64,
        )

    def encode(self, record):
        """Characterize ordinary dataclass JSON until the explicit adapter exists."""
        name = 'tasks.integration_s3_wire'
        if importlib.util.find_spec(name):
            return importlib.import_module(name).encode_source_record(record)
        return json.dumps(asdict(record), default=str).encode('utf-8')

    def test_metadata_retains_typed_owner_source_class_and_literal_link(self):
        """A generic dataclass dictionary loses the identity domain needed downstream."""
        record = self.metadata()
        encoded = self.encode(record)
        self.assertIs(type(encoded), bytes)
        payload = json.loads(encoded)
        body = payload.get('record', payload)
        self.assertEqual(body['owner'].get('domain'), 'work')
        self.assertEqual(body['identity']['domain'], 'attachment')
        self.assertEqual(body['identity']['environment'], record.identity.environment)
        self.assertEqual(body['owner']['environment'], record.owner.environment)
        self.assertEqual(body['source_class'], 'native_attachment')
        self.assertEqual(body['external_link'], record.external_link)
        self.assertEqual(body['comment'], record.comment)
        self.assertIsNone(body['filename'])
        self.assertEqual(body['upload_date'], '2020-01-02')
        self.assertEqual(body['schema_version'], record.schema_version)
        self.assertEqual(payload['wire_schema'], 'aimms.s3.source-wire.v1')
        self.assertNotIn('authority', body)
        self.assertNotIn('complete', body)

    def test_content_round_trip_preserves_original_bytes_and_distinct_digest(self):
        """Binary and zero-byte files retain content hashes, not metadata hashes."""
        for content in (b'\x00\xfforiginal\r\nnot UTF-8', b''):
            with self.subTest(size=len(content)):
                metadata = replace(
                    self.metadata(),
                    kind='file_and_link',
                    filename='Literal 站.bin',
                    file_size=len(content),
                )
                record = DocumentContentSourceRecord(
                    metadata=metadata,
                    content=content,
                    content_sha256=sha256(content).hexdigest(),
                )
                payload = json.loads(self.encode(record))['record']
                self.assertEqual(payload['schema_version'], record.schema_version)
                self.assertEqual(payload['metadata']['kind'], 'file_and_link')
                self.assertEqual(payload['metadata']['owner']['domain'], 'work')
                self.assertEqual(
                    payload['metadata']['external_link'], metadata.external_link
                )
                encoded = payload['content']
                self.assertEqual(encoded['encoding'], 'base64')
                self.assertEqual(b64decode(encoded['data'], validate=True), content)
                self.assertEqual(encoded['byte_size'], len(content))
                self.assertEqual(encoded['sha256'], sha256(content).hexdigest())
                self.assertNotEqual(encoded['sha256'], metadata.metadata_revision)

    def work(self):
        """Use independent direct field values, exact offsets and meaningful zeroes."""
        clock = datetime(
            2020, 1, 2, 3, 4, 5, 123456, tzinfo=timezone(timedelta(hours=5, minutes=30))
        )
        return WorkDetailsSourceRecord(
            identity=WorkSourceIdentity(environment='s3-wire', pk=7),
            reference=None,
            title='Synthetic literal title',
            description='Untrusted text <script>',
            status='backlog',
            lifecycle_status='draft',
            is_active=False,
            priority='medium',
            due_date=date(2020, 1, 3),
            work_order_type='corrective',
            created_at=clock,
            updated_at=clock,
            scheduled_start=None,
            scheduled_end=None,
            actual_started_at=None,
            actual_completed_at=None,
            estimated_minutes=0,
            lifecycle_version=0,
            hold_reason='',
            affected_component='',
            affected_component_ref='',
        )

    def test_direct_fields_round_trip_keeps_null_zero_and_exact_source_clocks(self):
        """Only declared direct fields travel; dates are not invented timestamps."""
        work = self.work()
        body = json.loads(self.encode(work))['record']
        self.assertEqual(body['identity']['domain'], 'work')
        self.assertEqual(body['schema_version'], work.schema_version)
        self.assertEqual(body['withheld_fields'], list(work.withheld_fields))
        fields = body['fields']
        self.assertEqual(
            set(fields),
            {
                'reference',
                'title',
                'description',
                'status',
                'lifecycle_status',
                'is_active',
                'priority',
                'due_date',
                'work_order_type',
                'created_at',
                'updated_at',
                'scheduled_start',
                'scheduled_end',
                'actual_started_at',
                'actual_completed_at',
                'estimated_minutes',
                'lifecycle_version',
                'hold_reason',
                'affected_component',
                'affected_component_ref',
            },
        )
        self.assertIsNone(fields['reference'])
        self.assertEqual(fields['hold_reason'], '')
        self.assertIsNone(fields['actual_completed_at'])
        assert_literal_bool(self, fields['is_active'], False)
        self.assertIs(type(fields['estimated_minutes']), int)
        self.assertEqual(fields['estimated_minutes'], 0)
        self.assertEqual(fields['lifecycle_version'], 0)
        self.assertEqual(fields['due_date'], '2020-01-03')
        self.assertEqual(fields['created_at'], '2020-01-02T03:04:05.123456+05:30')
        naive = replace(work, created_at=work.created_at.replace(tzinfo=None))
        self.assertEqual(
            json.loads(self.encode(naive))['record']['fields']['created_at'],
            '2020-01-02T03:04:05.123456',
        )
        maintenance = MaintenanceDetailsSourceRecord(
            identity=MaintenanceSourceIdentity(environment='s3-wire', pk=7),
            date=date(2020, 1, 2),
            summary='Synthetic event',
            details='Literal history',
            created_at=work.created_at,
            updated_at=work.updated_at,
        )
        body = json.loads(self.encode(maintenance))['record']
        self.assertEqual(body['identity']['domain'], 'maintenance')
        self.assertEqual(body['schema_version'], maintenance.schema_version)
        self.assertEqual(body['withheld_fields'], list(maintenance.withheld_fields))
        self.assertEqual(
            set(body['fields']),
            {'date', 'summary', 'details', 'created_at', 'updated_at'},
        )
        self.assertEqual(body['fields']['date'], '2020-01-02')

    def test_machine_owner_keeps_domain_despite_equal_numbered_ids(self):
        """Machine ownership is not inferred from matching Work numeric keys."""
        work_metadata = self.metadata()
        machine_metadata = replace(
            work_metadata,
            owner=MachineSourceIdentity(
                environment=work_metadata.identity.environment, pk=7
            ),
        )
        work = json.loads(self.encode(work_metadata))['record']['owner']
        machine = json.loads(self.encode(machine_metadata))['record']['owner']
        self.assertEqual(work['pk'], machine['pk'])
        self.assertNotEqual(work, machine)
        self.assertEqual(machine['domain'], 'machine')

    def test_metadata_fields_are_explicit_without_private_coords_or_authority(self):
        """Do not serialize source internals or silently add a future field."""
        record = self.metadata()
        object.__setattr__(record, 'storage_reference', 'Synthetic private coordinate')
        encoded = self.encode(record)
        self.assertNotIn(b'Synthetic private coordinate', encoded)
        self.assertEqual(
            set(json.loads(encoded)['record']),
            {
                'schema_version',
                'source_class',
                'identity',
                'owner',
                'kind',
                'filename',
                'external_link',
                'comment',
                'upload_date',
                'file_size',
                'is_image',
                'metadata_revision',
            },
        )

    def test_withheld_labels_refuse_equality_spoofing(self):
        """A str subclass cannot substitute a different declared withholding label."""
        from tasks.integration_s3_wire import SourceSerializationError

        class SpoofLabel(str):
            __hash__ = str.__hash__

            def __eq__(self, other):
                return True

        work = self.work()
        self.assertIs(type(self.encode(work)), bytes)
        object.__setattr__(
            work,
            'withheld_fields',
            (SpoofLabel('Unrecognized synthetic field'), *work.withheld_fields[1:]),
        )
        with self.assertRaises(SourceSerializationError):
            self.encode(work)

    def test_unknown_carriers_are_faults_not_empty_success(self):
        """None, dictionaries and arbitrary objects are not valid source records."""
        from tasks.integration_s3_wire import SourceSerializationError

        for record in (None, {}, False, object()):
            with self.subTest(carrier=type(record).__name__):
                with self.assertRaises(SourceSerializationError) as raised:
                    self.encode(record)
                self.assertEqual(
                    str(raised.exception), 'Source observation is unavailable'
                )

    def test_tampered_class_schema_scalars_identity_and_digest_are_not_encoded(self):
        """Revalidate observations; do not repair or relabel corrupted source values."""
        from tasks.integration_s3_wire import SourceSerializationError

        content = DocumentContentSourceRecord(
            metadata=replace(
                self.metadata(),
                kind='file',
                filename='Synthetic.bin',
                external_link=None,
            ),
            content=b'Exact bytes',
            content_sha256=sha256(b'Exact bytes').hexdigest(),
        )
        cases = (
            (self.metadata(), 'source_class', 'controlled_document'),
            (self.metadata(), 'schema_version', 'wrong-schema'),
            (self.metadata(), 'file_size', True),
            (self.metadata(), 'identity.pk', False),
            (self.work(), 'estimated_minutes', True),
            (self.work(), 'withheld_fields', ()),
            (content, 'content_sha256', 'b' * 64),
        )
        for record, name, value in cases:
            with self.subTest(field=name):
                self.assertIs(type(self.encode(record)), bytes)
                if name == 'identity.pk':
                    assert isinstance(record, DocumentMetadataSourceRecord)
                    object.__setattr__(record.identity, 'pk', value)
                else:
                    object.__setattr__(record, name, value)
                with self.assertRaises(SourceSerializationError):
                    self.encode(record)
