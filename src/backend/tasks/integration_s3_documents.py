"""Unwired S3 document metadata behind existing source authorization.

Actor and environment must be bound by a trusted caller. These operations do
not change native routes or supply historical attribution. The existing proof
filters remain authoritative; simulated-attribution tests are downstream only.
"""

import hashlib
from dataclasses import dataclass, field, fields
from datetime import date
from pathlib import PurePosixPath
from types import SimpleNamespace

from assets.integration_contracts import AttachmentSourceIdentity, MachineSourceIdentity
from tasks.integration_sources import WorkSourceIdentity


class DocumentMetadataUnavailableError(Exception):
    """The authorized source has invalid or unavailable document metadata."""


@dataclass(frozen=True)
class DocumentMetadataSourceRecord:
    """A direct-owner metadata observation, never a storage/content capability.

    Link text is preserved but never fetched. File presence is a stored-reference
    fact, not proof of byte availability. Metadata revision is the native editing
    token, not a digest of bytes. Omitted personnel, tags, plugin metadata,
    thumbnail, MIME and indirect associations are not implicitly authorized.
    """

    identity: AttachmentSourceIdentity
    owner: WorkSourceIdentity | MachineSourceIdentity
    kind: str
    filename: str | None = field(repr=False)
    external_link: str | None = field(repr=False)
    comment: str = field(repr=False)
    upload_date: date | None
    file_size: int
    is_image: bool
    metadata_revision: str = field(repr=False)
    schema_version: str = field(default='aimms.document.metadata.v1', init=False)
    source_class: str = field(default='native_attachment', init=False)

    def __post_init__(self):
        """Reject inconsistent identities and metadata without coercion."""
        if type(self.identity) is not AttachmentSourceIdentity:
            raise ValueError('Attachment source identity is required')
        if type(self.owner) not in (WorkSourceIdentity, MachineSourceIdentity):
            raise ValueError('Supported typed document owner is required')
        if self.owner.environment != self.identity.environment:
            raise ValueError('Document and owner must share a source environment')
        if type(self.kind) is not str or self.kind not in (
            'file',
            'link',
            'file_and_link',
        ):
            raise ValueError('Invalid document kind')
        if self.filename is not None and type(self.filename) is not str:
            raise ValueError('Invalid document filename')
        if self.external_link is not None and type(self.external_link) is not str:
            raise ValueError('Invalid external link')
        if bool(self.filename) != (self.kind in ('file', 'file_and_link')):
            raise ValueError('Document kind and file presence disagree')
        if bool(self.external_link) != (self.kind in ('link', 'file_and_link')):
            raise ValueError('Document kind and link presence disagree')
        if type(self.comment) is not str:
            raise ValueError('Invalid document comment')
        if self.upload_date is not None and type(self.upload_date) is not date:
            raise ValueError('Upload date must remain a date')
        if type(self.file_size) is not int or self.file_size < 0:
            raise ValueError('Invalid source file size')
        if type(self.is_image) is not bool:
            raise ValueError('Invalid source image flag')
        if (
            type(self.metadata_revision) is not str
            or len(self.metadata_revision) != 64
            or any(char not in '0123456789abcdef' for char in self.metadata_revision)
        ):
            raise ValueError('Invalid native metadata revision')


def _metadata_snapshot(identity, owner, model_type):
    """Read one exact owner edge without constructing a media URL."""
    from common.models import Attachment

    return (
        Attachment.objects
        .filter(pk=identity.pk, model_type=model_type, model_id=owner.pk)
        .values(
            'model_type',
            'model_id',
            'attachment',
            'link',
            'comment',
            'upload_date',
            'file_size',
            'is_image',
        )
        .first()
    )


def _metadata_values(identity, owner, row):
    """Calculate primitive metadata without another DTO constructor or I/O."""
    from common.attachment_revision import attachment_revision

    reference = row['attachment']
    link = row['link']
    if reference and '\\' in reference:
        raise DocumentMetadataUnavailableError('Document metadata is unavailable')
    if not reference and not link:
        raise DocumentMetadataUnavailableError('Document metadata is unavailable')
    return {
        'identity': identity,
        'owner': owner,
        'kind': 'file_and_link'
        if reference and link
        else 'file'
        if reference
        else 'link',
        'filename': PurePosixPath(reference).name if reference else None,
        'external_link': link,
        'comment': row['comment'],
        'upload_date': row['upload_date'],
        'file_size': row['file_size'],
        'is_image': row['is_image'],
        'metadata_revision': attachment_revision(
            SimpleNamespace(
                pk=identity.pk,
                model_type=row['model_type'],
                model_id=row['model_id'],
                attachment=reference or '',
                link=link,
                comment=row['comment'],
            )
        ),
    }


def _project_metadata(identity, owner, row):
    """Detach authorized native metadata without copying storage coordinates."""
    try:
        return DocumentMetadataSourceRecord(**_metadata_values(identity, owner, row))
    except (TypeError, ValueError):
        raise DocumentMetadataUnavailableError(
            'Document metadata is unavailable'
        ) from None


def _metadata_key(record):
    return tuple(
        (type(getattr(record, f.name)), getattr(record, f.name))
        for f in fields(DocumentMetadataSourceRecord)
    )


def _metadata_matches_row(
    record, *, environment, attachment_pk, owner_type, owner_pk, row
):
    """Bind a private DTO to literal native identity and raw metadata facts."""
    if (
        type(record) is not DocumentMetadataSourceRecord
        or type(record.identity) is not AttachmentSourceIdentity
        or type(record.identity.environment) is not str
        or record.identity.environment != environment
        or type(record.identity.pk) is not int
        or record.identity.pk != attachment_pk
        or owner_type not in (WorkSourceIdentity, MachineSourceIdentity)
        or type(record.owner) is not owner_type
        or type(record.owner.environment) is not str
        or record.owner.environment != environment
        or type(record.owner.pk) is not int
        or record.owner.pk != owner_pk
        or type(record.schema_version) is not str
        or record.schema_version != 'aimms.document.metadata.v1'
        or type(record.source_class) is not str
        or record.source_class != 'native_attachment'
    ):
        return False
    try:
        values = _metadata_values(record.identity, record.owner, row)
    except (TypeError, ValueError, DocumentMetadataUnavailableError):
        return False
    return all(
        type(getattr(record, name)) is type(value) and getattr(record, name) == value
        for name, value in values.items()
    )


def document_metadata_matches(actor, *, environment, attachment_pk, expected):
    """Recheck a wire observation without recursively reconstructing metadata.

    This predicate is a fresh native owner/proof read plus primitive field and
    revision comparison. It creates no authority cache and never skips the
    original storage fences or a later delivery/await revalidation.
    """
    from assets.integration_sources import read_machine_attachment
    from tasks.integration_attachments import read_work_attachment

    if type(expected) is not DocumentMetadataSourceRecord:
        return False
    work = type(expected.owner) is WorkSourceIdentity
    if not work and type(expected.owner) is not MachineSourceIdentity:
        return False
    reader = read_work_attachment if work else read_machine_attachment
    current = reader(actor, environment=environment, attachment_pk=attachment_pk)
    if (
        current is None
        or current.identity != expected.identity
        or getattr(current, 'work' if work else 'machine') != expected.owner
    ):
        return False
    row = _metadata_snapshot(
        current.identity, expected.owner, 'workorder' if work else 'assetmachine'
    )
    if row is None:
        return False
    return _metadata_matches_row(
        expected,
        environment=environment,
        attachment_pk=attachment_pk,
        owner_type=type(expected.owner),
        owner_pk=getattr(current, 'work' if work else 'machine').pk,
        row=row,
    )


def _read_document(actor, environment, attachment_pk, reader, model_type, owner_field):
    """Compose exact direct-owner operations, not caller-supplied grants."""
    first = reader(actor, environment=environment, attachment_pk=attachment_pk)
    if first is None:
        return None
    owner = getattr(first, owner_field)
    owner_type, owner_pk = type(owner), owner.pk
    row = _metadata_snapshot(first.identity, owner, model_type)
    if row is None:
        return None

    def current_matches():
        current = reader(actor, environment=environment, attachment_pk=attachment_pk)
        return (
            current is not None
            and current == first
            and _metadata_snapshot(first.identity, owner, model_type) == row
        )

    # Projection below is synchronous and private: it performs no storage I/O
    # or delivery. The fresh check on return (and on a validation fault) binds
    # the projected row to current authority without recursively repeating the
    # entire owner/proof reader before the same pure operation.
    try:
        projected = _project_metadata(first.identity, owner, row)
    except DocumentMetadataUnavailableError:
        if not current_matches():
            return None
        raise
    if not current_matches() or not _metadata_matches_row(
        projected,
        environment=environment,
        attachment_pk=attachment_pk,
        owner_type=owner_type,
        owner_pk=owner_pk,
        row=row,
    ):
        return None
    return projected


def read_work_document(actor, *, environment, attachment_pk):
    """Reauthorize direct Work metadata separately for this operation.

    The default policy withholds every unproven historical owner. Successful
    sequential observations are not an atomic DB/storage/authority snapshot.
    A prior owner identity is never accepted as a reusable authorization token.
    """
    from tasks.integration_attachments import read_work_attachment

    return _read_document(
        actor, environment, attachment_pk, read_work_attachment, 'workorder', 'work'
    )


def read_machine_document(actor, *, environment, attachment_pk):
    """Reauthorize direct machine metadata without changing the S1 reader."""
    from assets.integration_sources import read_machine_attachment

    return _read_document(
        actor,
        environment,
        attachment_pk,
        read_machine_attachment,
        'assetmachine',
        'machine',
    )


@dataclass(frozen=True)
class DocumentContentSourceRecord:
    """Exact bounded original bytes bound to a separately authorized observation.

    The digest identifies returned bytes, not immutable storage or future access.
    Generated repr suppression is not serialization or arbitrary-log redaction.
    """

    metadata: DocumentMetadataSourceRecord
    content: bytes = field(repr=False)
    content_sha256: str = field(repr=False)
    schema_version: str = field(default='aimms.document.content.v1', init=False)

    def __post_init__(self):
        """Reject a link masquerading as a file or a mismatched byte digest."""
        if type(self.metadata) is not DocumentMetadataSourceRecord:
            raise ValueError('Document metadata observation is required')
        if self.metadata.kind == 'link' or type(self.content) is not bytes:
            raise ValueError('Stored-file bytes are required')
        if (
            type(self.content_sha256) is not str
            or hashlib.sha256(self.content).hexdigest() != self.content_sha256
        ):
            raise ValueError('Content digest must match returned bytes')


def _read_content(
    actor, environment, attachment_pk, max_bytes, owner_reader, owner_field, model_type
):
    """Compose private document DTOs inside the native bounded read fences."""
    from assets.integration_content_read import (
        _original_assembly,
        _OriginalWithheldError,
    )
    from assets.integration_sources import (
        AttachmentContentAbsentError,
        AttachmentContentLimitError,
        AttachmentContentUnavailableError,
    )

    before = row = metadata_key = None

    def metadata_current():
        return (
            _metadata_matches_row(
                before,
                environment=environment,
                attachment_pk=attachment_pk,
                owner_type=owner_type,
                owner_pk=owner_pk,
                row=row,
            )
            and _metadata_snapshot(attachment_identity, owner_identity, model_type)
            == row
            and _metadata_key(before) == metadata_key
        )

    def content_matches():
        return (
            type(projected) is DocumentContentSourceRecord
            and projected.metadata is before
            and type(projected.content) is bytes
            and projected.content == content
            and type(projected.content_sha256) is str
            and projected.content_sha256 == digest
            and type(projected.schema_version) is str
            and projected.schema_version == 'aimms.document.content.v1'
        )

    try:
        with _original_assembly(
            actor,
            environment=environment,
            attachment_pk=attachment_pk,
            max_bytes=max_bytes,
            owner_reader=owner_reader,
            model_type=model_type,
            owner_field=owner_field,
        ) as assembly:
            attachment_identity = assembly.identity
            owner_identity = assembly.owner
            owner_type, owner_pk = type(owner_identity), owner_identity.pk
            row = _metadata_snapshot(attachment_identity, owner_identity, model_type)
            if row is None:
                raise _OriginalWithheldError()
            before = _project_metadata(attachment_identity, owner_identity, row)
            if type(before) is not DocumentMetadataSourceRecord:
                raise DocumentMetadataUnavailableError(
                    'Document metadata is unavailable'
                )
            metadata_key = _metadata_key(before)
            if not metadata_current():
                raise _OriginalWithheldError()
            content = assembly.read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            try:
                projected = DocumentContentSourceRecord(
                    metadata=before, content=content, content_sha256=digest
                )
            except (TypeError, ValueError):
                raise AttachmentContentUnavailableError(
                    'Attachment content is unavailable'
                ) from None
            if not content_matches():
                raise AttachmentContentUnavailableError(
                    'Attachment content is unavailable'
                )
        # Native return/fault checks stay inside the owner of the storage read.
        # Metadata and projection checks after that fence bind the exact bytes
        # without reconstructing a nested public reader or caching authority.
        if not metadata_current():
            return None
        if not content_matches():
            raise AttachmentContentUnavailableError('Attachment content is unavailable')
        return projected
    except _OriginalWithheldError:
        return None
    except (
        AttachmentContentAbsentError,
        AttachmentContentLimitError,
        AttachmentContentUnavailableError,
        DocumentMetadataUnavailableError,
    ):
        if row is not None and (
            _metadata_snapshot(attachment_identity, owner_identity, model_type) != row
            or (
                before is not None
                and metadata_key is not None
                and _metadata_key(before) != metadata_key
            )
        ):
            return None
        raise


def read_work_document_content(actor, *, environment, attachment_pk, max_bytes):
    """Read bounded Work bytes with current metadata, never an external URL."""
    from tasks.integration_attachments import read_work_attachment

    return _read_content(
        actor,
        environment,
        attachment_pk,
        max_bytes,
        read_work_attachment,
        'work',
        'workorder',
    )


def read_machine_document_content(actor, *, environment, attachment_pk, max_bytes):
    """Read direct machine bytes without bypassing its source proof gate."""
    from assets.integration_sources import read_machine_attachment

    return _read_content(
        actor,
        environment,
        attachment_pk,
        max_bytes,
        read_machine_attachment,
        'machine',
        'assetmachine',
    )
