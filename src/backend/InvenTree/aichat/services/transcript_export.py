"""Bounded, complete-before-response transcript downloads for the browser."""

import json
from io import BytesIO

from django.contrib.auth import get_user_model

MAX_EXPORT_BYTES = 16 * 1024 * 1024
MAX_EXPORT_RECORDS = 10000


class ExportTooLargeError(ValueError):
    """The transcript needs the existing operator export path."""


class ExportIncompleteError(ValueError):
    """No complete artifact may be returned."""


class ExportAccessError(ValueError):
    """The account is no longer eligible for a browser download."""


def browser_transcript_export(repository):
    """Build bounded JSON without publishing a partial response or server file.

    The repository projection is a live read with a creation cutoff, not a
    transactionally consistent snapshot. Limits apply to serialized bytes and
    records; an individual ORM field/JSON encoding can allocate additional RAM.
    """
    users = get_user_model().objects.filter(pk=repository.actor_id, is_active=True)
    if not users.exists():
        raise ExportAccessError('Export account is unavailable')
    stream = BytesIO()

    def write(value):
        if stream.tell() + len(value) > MAX_EXPORT_BYTES:
            raise ExportTooLargeError('Transcript download exceeds its limit')
        stream.write(value)

    write(b'{"schema_version":1,"records":[')
    threads = messages = 0
    complete = False
    for count, record in enumerate(
        repository.export_transcript_records(chunk_size=100), start=1
    ):
        if count > MAX_EXPORT_RECORDS:
            raise ExportTooLargeError('Transcript download exceeds its limit')
        kind = record.get('type')
        if complete or (count == 1 and kind != 'manifest'):
            raise ExportIncompleteError('Invalid transcript record order')
        if kind == 'manifest':
            if (
                count != 1
                or record.get('scope') != 'owned_thread_transcripts'
                or record.get('schema_version') != 1
            ):
                raise ExportIncompleteError('Invalid transcript manifest')
        elif kind == 'thread':
            threads += 1
        elif kind == 'message':
            messages += 1
        elif kind == 'complete':
            if (
                type(record.get('threads')) is not int
                or type(record.get('messages')) is not int
                or record.get('threads') != threads
                or record.get('messages') != messages
            ):
                raise ExportIncompleteError('Incomplete transcript counts')
            complete = True
        else:
            raise ExportIncompleteError('Unsupported transcript record')
        if count > 1:
            write(b',\n')
        write(json.dumps(record, ensure_ascii=False).encode('utf-8'))
    if not complete:
        raise ExportIncompleteError('Transcript export did not finish')
    write(b']}\n')
    # No content has left this process. Deactivation during the read cancels
    # the whole response; the operator-only export remains independently usable.
    if not users.exists():
        raise ExportAccessError('Export account is unavailable')
    return stream.getvalue()
