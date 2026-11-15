"""Private synchronous assembly inside fresh bounded native original checks.

This is not an authorization lease, public callback hook or resumable reader.
Native entry/each storage operation/return or ordinary fault reobserve the exact
owner. Only the source-owned native and document readers compose private DTOs
inside this scope. No authority or bytes survive as a reusable read capability.
"""

import hashlib
from contextlib import contextmanager

from tasks.integration_sources import WorkSourceIdentity

from assets.integration_contracts import AttachmentSourceIdentity, MachineSourceIdentity
from assets.integration_sources import (
    AttachmentContentAbsentError,
    AttachmentContentLimitError,
    AttachmentContentUnavailableError,
)


class _OriginalWithheldError(Exception):
    """Internal denial; callers return their existing unavailable observation."""


class _OriginalAssembly:
    """One active, one-read synchronous private assembly, never read authority."""

    def __init__(self, identity, owner, read):
        self.identity = identity
        self.owner = owner
        self._read = read
        self._active = True
        self._used = False

    def read_bytes(self):
        """Require a new native check before the single bounded storage read."""
        if not self._active or self._used:
            raise AttachmentContentUnavailableError('Stored content is unavailable')
        self._used = True
        return self._read()

    def close(self):
        self._active = False

    def __reduce__(self):
        raise TypeError('Private original assembly cannot be serialized')

    def __copy__(self):
        raise TypeError('Private original assembly cannot be copied')

    def __deepcopy__(self, memo):
        raise TypeError('Private original assembly cannot be copied')


def _projection_matches(
    projected, *, record_type, identity, owner, owner_field, content
):
    """Compare literal captured values after every fallible native projection."""
    return (
        type(projected) is record_type
        and type(projected.identity) is AttachmentSourceIdentity
        and type(projected.identity.environment) is str
        and projected.identity.environment == identity[0]
        and type(projected.identity.pk) is int
        and projected.identity.pk == identity[1]
        and type(getattr(projected, owner_field)) is owner[0]
        and type(getattr(projected, owner_field).environment) is str
        and getattr(projected, owner_field).environment == owner[1]
        and type(getattr(projected, owner_field).pk) is int
        and getattr(projected, owner_field).pk == owner[2]
        and type(projected.content) is bytes
        and projected.content == content
        and type(projected.content_sha256) is str
        and projected.content_sha256 == hashlib.sha256(content).hexdigest()
        and type(projected.schema_version) is str
        and projected.schema_version
        == f'aimms.{"work" if owner_field == "work" else "machine"}-attachment.content.v1'
    )


@contextmanager
def _original_assembly(
    actor,
    *,
    environment,
    attachment_pk,
    max_bytes,
    owner_reader,
    model_type,
    owner_field,
):
    """Keep private projections inside entry/pre-storage/return native fences.

    The trusted native caller fixes reader/model/owner. Snapshot only literal
    selection coordinates and stored name, never an authorization decision.
    There is no await, storage write, caller-selected source path or retry.

    Yields:
        An active private assembly, invalidated before leaving the synchronous call.
    """
    from django.core.files.storage import default_storage

    from assets import integration_sources
    from common.models import Attachment

    if owner_field == 'work':
        from tasks import integration_attachments as budget_owner
    else:
        budget_owner = integration_sources
    if (
        type(budget_owner.MAX_MACHINE_ATTACHMENT_CONTENT_BYTES) is not int
        or type(max_bytes) is not int
        or not 1
        <= max_bytes
        <= min(1024 * 1024, budget_owner.MAX_MACHINE_ATTACHMENT_CONTENT_BYTES)
        or (model_type, owner_field)
        not in (('workorder', 'work'), ('assetmachine', 'machine'))
    ):
        raise ValueError('Attachment read outside supported bounds')
    current_owner = owner_reader(
        actor, environment=environment, attachment_pk=attachment_pk
    )
    if current_owner is None:
        raise _OriginalWithheldError()
    identity = current_owner.identity
    owner = getattr(current_owner, owner_field)
    expected_owner_type = (
        WorkSourceIdentity if owner_field == 'work' else MachineSourceIdentity
    )
    if (
        type(identity) is not AttachmentSourceIdentity
        or type(identity.environment) is not str
        or identity.environment != environment
        or type(identity.pk) is not int
        or identity.pk != attachment_pk
        or type(owner) is not expected_owner_type
        or type(owner.pk) is not int
        or owner.pk < 1
        or type(owner.environment) is not str
        or owner.environment != environment
    ):
        raise _OriginalWithheldError()
    owner_pk = owner.pk

    def stored_name():
        return (
            Attachment.objects
            .filter(pk=attachment_pk, model_type=model_type, model_id=owner_pk)
            .values('attachment')
            .first()
        )

    row = stored_name()
    if row is None:
        raise _OriginalWithheldError()
    name = row['attachment']

    def current():
        latest = owner_reader(
            actor, environment=environment, attachment_pk=attachment_pk
        )
        return (
            latest is not None
            and type(latest.identity) is AttachmentSourceIdentity
            and type(latest.identity.pk) is int
            and latest.identity.pk == attachment_pk
            and type(latest.identity.environment) is str
            and latest.identity.environment == environment
            and type(getattr(latest, owner_field)) is expected_owner_type
            and type(getattr(latest, owner_field).pk) is int
            and getattr(latest, owner_field).pk == owner_pk
            and type(getattr(latest, owner_field).environment) is str
            and getattr(latest, owner_field).environment == environment
            and stored_name() == row
        )

    def read():
        if not current():
            raise _OriginalWithheldError()
        if not name:
            raise AttachmentContentAbsentError('No stored content')
        try:
            with default_storage.open(name, 'rb') as handle:
                blocks, remaining, eof = [], max_bytes + 1, False
                read_limit = budget_owner.MAX_MACHINE_ATTACHMENT_CONTENT_READS
                if type(read_limit) is not int or not 1 <= read_limit <= 1024:
                    raise AttachmentContentUnavailableError(
                        'Stored content is unavailable'
                    )
                for _ in range(read_limit):
                    block = handle.read(remaining)
                    if not isinstance(block, bytes) or len(block) > remaining:
                        raise AttachmentContentUnavailableError(
                            'Stored content is unavailable'
                        )
                    if not block:
                        eof = True
                        break
                    blocks.append(block)
                    remaining -= len(block)
                    if not remaining:
                        break
                content = b''.join(blocks)
        except MemoryError:
            raise
        except AttachmentContentUnavailableError:
            raise
        except Exception:
            raise AttachmentContentUnavailableError(
                'Stored content is unavailable'
            ) from None
        if len(content) > max_bytes:
            raise AttachmentContentLimitError(
                'Stored content exceeds the requested limit'
            )
        if not eof:
            raise AttachmentContentUnavailableError('Storage read budget exhausted')
        return content

    assembly = _OriginalAssembly(identity, owner, read)
    try:
        yield assembly
    except (MemoryError, KeyboardInterrupt, SystemExit):
        raise
    except Exception:
        if not current():
            raise _OriginalWithheldError() from None
        raise
    else:
        if not current():
            raise _OriginalWithheldError()
    finally:
        assembly.close()
