"""Explicit S3 wire projections; observations are never access credentials.

This explicit boundary supports the default-off S4 source reader; it installs
no HTTP route, retrieval provider or source enrollment.
Pure encoding does not authenticate a caller. Use the reauthorized read helpers
for actor-bound observations; neither form confers historical attribution.
"""

import json
from base64 import b64encode
from datetime import date, datetime
from functools import partial

from assets.integration_contracts import MachineSourceIdentity
from tasks.integration_s3_context import (
    CONTEXT_FIELDS,
    WorkContextSourceRecord,
    read_work_context,
)
from tasks.integration_s3_details import (
    MaintenanceDetailsSourceRecord,
    WorkDetailsSourceRecord,
    read_maintenance_details,
    read_work_details,
)
from tasks.integration_s3_documents import (
    DocumentContentSourceRecord,
    DocumentMetadataSourceRecord,
    document_metadata_matches,
    read_machine_document,
    read_machine_document_content,
    read_work_document,
    read_work_document_content,
)
from tasks.integration_sources import WorkSourceIdentity

WIRE_SCHEMA = 'aimms.s3.source-wire.v1'
_WORK_WIRE_FIELDS = (
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
)


class SourceSerializationError(Exception):
    """An authorized source observation could not be encoded safely."""


def _identity(value, domain):
    type(value).__post_init__(value)
    return {'domain': domain, 'environment': value.environment, 'pk': value.pk}


def _metadata(record):
    DocumentMetadataSourceRecord.__post_init__(record)
    if (
        type(record.schema_version) is not str
        or record.schema_version != 'aimms.document.metadata.v1'
        or type(record.source_class) is not str
        or record.source_class != 'native_attachment'
    ):
        raise ValueError('Unsupported source classification')
    owner_domain = {WorkSourceIdentity: 'work', MachineSourceIdentity: 'machine'}[
        type(record.owner)
    ]
    return {
        'schema_version': record.schema_version,
        'source_class': record.source_class,
        'identity': _identity(record.identity, 'attachment'),
        'owner': _identity(record.owner, owner_domain),
        'kind': record.kind,
        'filename': record.filename,
        'external_link': record.external_link,
        'comment': record.comment,
        'upload_date': (
            record.upload_date.isoformat() if record.upload_date is not None else None
        ),
        'file_size': record.file_size,
        'is_image': record.is_image,
        'metadata_revision': record.metadata_revision,
    }


def _content(record):
    DocumentContentSourceRecord.__post_init__(record)
    if (
        type(record.schema_version) is not str
        or record.schema_version != 'aimms.document.content.v1'
    ):
        raise ValueError('Unsupported content schema')
    return {
        'schema_version': record.schema_version,
        'metadata': _metadata(record.metadata),
        'content': {
            'encoding': 'base64',
            'data': b64encode(record.content).decode('ascii'),
            'byte_size': len(record.content),
            'sha256': record.content_sha256,
        },
    }


def _direct(record, domain, names, schema):
    type(record).__post_init__(record)
    expected_withheld = type(record).__dataclass_fields__['withheld_fields'].default
    if (
        type(record.schema_version) is not str
        or record.schema_version != schema
        or type(record.withheld_fields) is not tuple
        or any(type(name) is not str for name in record.withheld_fields)
        or record.withheld_fields != expected_withheld
    ):
        raise ValueError('Unsupported direct observation')
    fields = {}
    for name in names:
        value = getattr(record, name)
        fields[name] = (
            value.isoformat() if isinstance(value, (date, datetime)) else value
        )
    return {
        'schema_version': record.schema_version,
        'identity': _identity(record.identity, domain),
        'withheld_fields': list(record.withheld_fields),
        'fields': fields,
    }


def encode_source_record(record) -> bytes:
    """Encode only explicit supported fields; this pure function grants no access."""
    try:
        if type(record) is DocumentMetadataSourceRecord:
            body = _metadata(record)
        elif type(record) is DocumentContentSourceRecord:
            body = _content(record)
        elif type(record) is WorkContextSourceRecord:
            body = _direct(
                record,
                'work',
                (*_WORK_WIRE_FIELDS, *CONTEXT_FIELDS),
                'aimms.work.context.v1',
            )
        elif type(record) is WorkDetailsSourceRecord:
            body = _direct(
                record, 'work', _WORK_WIRE_FIELDS, 'aimms.work.core_details.v1'
            )
        elif type(record) is MaintenanceDetailsSourceRecord:
            body = _direct(
                record,
                'maintenance',
                ('date', 'summary', 'details', 'created_at', 'updated_at'),
                'aimms.maintenance.direct_details.v1',
            )
        else:
            raise ValueError('Unsupported S3 observation')
        return json.dumps(
            {'wire_schema': WIRE_SCHEMA, 'record': body},
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(',', ':'),
        ).encode('utf-8')
    except (AttributeError, TypeError, ValueError):
        raise SourceSerializationError('Source observation is unavailable') from None


def _read_json(read, observe, *, matches=None) -> bytes | None:
    record = read()
    if record is None:
        return None
    expected = (
        record.metadata if type(record) is DocumentContentSourceRecord else record
    )

    def current():
        return (
            matches(expected=expected) if matches is not None else observe() == expected
        )

    try:
        encoded = encode_source_record(record)
        if type(encoded) is not bytes:
            raise SourceSerializationError('Source observation is unavailable')
    except SourceSerializationError:
        if not current():
            return None
        raise
    if not current():
        return None
    return encoded


def read_work_document_metadata_json(
    actor, *, environment, attachment_pk
) -> bytes | None:
    """Encode current direct Work metadata, then recheck source and actor authority."""
    read = partial(
        read_work_document, actor, environment=environment, attachment_pk=attachment_pk
    )
    return _read_json(read, read)


def read_machine_document_metadata_json(
    actor, *, environment, attachment_pk
) -> bytes | None:
    """Encode current typed machine metadata, without adding related Work access."""
    read = partial(
        read_machine_document,
        actor,
        environment=environment,
        attachment_pk=attachment_pk,
    )
    return _read_json(read, read)


def read_work_document_content_json(
    actor, *, environment, attachment_pk, max_bytes
) -> bytes | None:
    """Encode bounded original Work bytes and recheck metadata without reopening."""
    read = partial(
        read_work_document_content,
        actor,
        environment=environment,
        attachment_pk=attachment_pk,
        max_bytes=max_bytes,
    )
    observe = partial(
        read_work_document, actor, environment=environment, attachment_pk=attachment_pk
    )
    return _read_json(
        read,
        observe,
        matches=partial(
            document_metadata_matches,
            actor,
            environment=environment,
            attachment_pk=attachment_pk,
        ),
    )


def read_machine_document_content_json(
    actor, *, environment, attachment_pk, max_bytes
) -> bytes | None:
    """Encode bounded typed machine bytes; do not fetch a document's external link."""
    read = partial(
        read_machine_document_content,
        actor,
        environment=environment,
        attachment_pk=attachment_pk,
        max_bytes=max_bytes,
    )
    observe = partial(
        read_machine_document,
        actor,
        environment=environment,
        attachment_pk=attachment_pk,
    )
    return _read_json(
        read,
        observe,
        matches=partial(
            document_metadata_matches,
            actor,
            environment=environment,
            attachment_pk=attachment_pk,
        ),
    )


def read_work_details_json(actor, *, environment, work_pk) -> bytes | None:
    """Encode direct Work fields without turning a source counter into a watermark."""
    read = partial(read_work_details, actor, environment=environment, work_pk=work_pk)
    return _read_json(read, read)


def read_maintenance_details_json(actor, *, environment, record_pk) -> bytes | None:
    """Encode direct history fields; omitted related/personnel data remains withheld."""
    read = partial(
        read_maintenance_details, actor, environment=environment, record_pk=record_pk
    )
    return _read_json(read, read)


def read_work_context_json(actor, *, environment, work_pk) -> bytes | None:
    """Encode independently checked context and recheck both endpoints afterward."""
    read = partial(read_work_context, actor, environment=environment, work_pk=work_pk)
    return _read_json(read, read)
