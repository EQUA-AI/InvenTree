"""Turn one pumphouse snapshot into normalized readings.

This is the single normalization path the gate asks for: the live Cosmos
connector and the offline dump importer both call it, so a value read from the
account and the same value read from a file cannot disagree.

The function is pure and does no I/O, which is what lets it be proved against
real payloads without an Azure account.

Two rules shape everything here.

**The raw payload is authoritative.** ``data1_raw`` holds the text the source
stored. When it is present the parsed fields are re-checked against it and a
disagreement fails the snapshot rather than being quietly preferred one way or
the other.

**A key's position decides what it measures.** ``/sl`` is the station's surge
pool level; ``/pd/P3/dv`` is pump 3's discharge. The external key is the
JSON-pointer where the value was found, which is exactly ``DictionaryPoint.path``
from the registry - so an approved dictionary point becomes a binding without a
second mapping table to keep in step.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone

from assets.health_models import SignalQuality
from machine_health.connectors.base import Reading
from machine_health.services.ingestion import MAX_READINGS_PER_BATCH, MAX_VALUE_BYTES

#: Envelope fields that describe the message rather than the plant. They are not
#: measurements, so they are not offered as readings unless a binding asks by
#: name - `st` is the exception, being a genuine station observation.
ENVELOPE_KEYS = frozenset({'sr', 'dsc', 'egt', 'ext', 'pd', 'dex', 'data2'})

#: Bookkeeping inside `dex`: the station's own id and a restatement of `egt`.
DEX_METADATA_KEYS = frozenset({'ID', 'TIMESTAMP'})

#: Confirmed status vocabulary. Anything else is passed through untranslated and
#: marked uncertain: guessing what an unknown plant code means is how a stopped
#: pump ends up displayed as running.
STATUS_CODES = {'I': 'Idle', 'R': 'Running'}


class SnapshotError(Exception):
    """A snapshot that cannot be trusted to describe what the source sent."""


def _pointer(segment: str) -> str:
    """Escape one JSON-pointer segment (RFC 6901).

    Real tags contain spaces and doubled prefixes; ``~`` and ``/`` have not been
    observed, but escaping them is what makes the key unambiguous rather than
    merely unambiguous so far.
    """
    return segment.replace('~', '~0').replace('/', '~1')


def _coerce(value):
    """Return ``(value, quality)`` for one measurement.

    ``dex`` transports every reading as a string, so numbers arrive quoted. A
    value that will not parse is kept as the original string and marked
    uncertain, never dropped and never replaced with zero: "unparsable" and
    "zero" are different facts about a plant.
    """
    if value is None:
        return None, SignalQuality.BAD

    if isinstance(value, bool):
        # A bool is an int in Python but not a measurement here; passing it
        # through as 1/0 would invent a number the source did not send.
        return value, SignalQuality.GOOD

    if isinstance(value, (int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return None, SignalQuality.BAD
        return value, SignalQuality.GOOD

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None, SignalQuality.BAD
        try:
            number = float(text)
        except ValueError:
            return value, SignalQuality.UNCERTAIN
        if not math.isfinite(number):
            return None, SignalQuality.BAD
        return number, SignalQuality.GOOD

    return None, SignalQuality.BAD


def _status(value):
    """Return ``(code, quality)`` for a status field, never a translation.

    The displayed label lives in :data:`STATUS_CODES`; the stored value stays the
    source's own code so that a vocabulary we learn later cannot retroactively
    change what was recorded.
    """
    if not isinstance(value, str) or not value:
        return None, SignalQuality.BAD
    known = value in STATUS_CODES
    return value, SignalQuality.GOOD if known else SignalQuality.UNCERTAIN


def observed_at(document) -> datetime:
    """Read the sample's own timestamp, in UTC.

    ``sub_time_period`` is when the source says the sample happened. Falling back
    to ``egt`` is acceptable - they are equal in every observed payload - but
    falling back to *now* is not: import time is not observation time, and a
    July 2025 snapshot must never surface as a fresh reading.
    """
    for key in ('sub_time_period', 'egt'):
        moment = document.get(key)
        if isinstance(moment, int) and not isinstance(moment, bool):
            return datetime.fromtimestamp(moment / 1000, tz=timezone.utc)
    raise SnapshotError('Snapshot carries no usable sample timestamp.')


def _verify_against_raw(document) -> None:
    """Fail the snapshot when parsed fields contradict the verbatim payload."""
    raw = document.get('data1_raw')
    if not raw:
        return

    try:
        payload = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise SnapshotError(f'data1_raw is not readable JSON: {exc}') from None

    for key, value in payload.items():
        if key in document and document[key] != value:
            raise SnapshotError(
                f'Parsed field {key!r} disagrees with data1_raw; the raw payload '
                'is authoritative, so this snapshot misrepresents the source.'
            )


def _fits(value) -> bool:
    """Whether a value survives the ingestion size bound."""
    if value is None or isinstance(value, (int, float, bool)):
        return True
    return len(str(value).encode('utf-8')) <= MAX_VALUE_BYTES


def flatten_snapshot(document, *, include_extension: bool = True) -> list[Reading]:
    """Return one :class:`Reading` per measurement in a snapshot.

    Args:
        document: a Cosmos document, or any mapping with the same payload shape.
        include_extension: read the ``dex`` tag map as well as the summary
            fields. Both are ingested by default; the flag exists so a caller
            can take the ~12 station values without the ~700 detail tags.

    Raises:
        SnapshotError: the snapshot contradicts its own raw payload or carries
            no sample timestamp. Refusing is the point - a snapshot we cannot
            vouch for should not become a reading someone acts on.
    """
    if not isinstance(document, dict):
        raise SnapshotError('A snapshot must be an object.')

    _verify_against_raw(document)
    moment = observed_at(document)
    sequence = document.get('sub_time_period')
    readings: list[Reading] = []

    def emit(path: str, value, quality) -> None:
        """Record one reading, dropping only values too large to store."""
        if not _fits(value):
            return
        readings.append(
            Reading(
                external_key=path,
                value=value,
                observed_at=moment,
                quality=quality,
                sequence=sequence if isinstance(sequence, int) else None,
            )
        )

    payload = document
    if 'data1_raw' in document:
        payload = json.loads(document['data1_raw'])

    # Station level: sl, dv, pmw, pmvar, pc and st sit at the root.
    for key, value in payload.items():
        if key in ENVELOPE_KEYS or key.startswith('_'):
            continue
        if key == 'st':
            emit('/st', *_status(value))
        elif not isinstance(value, (dict, list)):
            emit(f'/{_pointer(key)}', *_coerce(value))

    # Pump level: the same measurement names, one level down, owned by a pump.
    for pump_key, summary in (payload.get('pd') or {}).items():
        if not isinstance(summary, dict):
            continue
        for tag, value in summary.items():
            path = f'/pd/{_pointer(pump_key)}/{_pointer(tag)}'
            if tag == 'st':
                emit(path, *_status(value))
            elif not isinstance(value, (dict, list)):
                emit(path, *_coerce(value))

    if not include_extension:
        return readings

    # Extension tags: detailed instrument readings, transported as strings.
    for tag, value in (payload.get('dex') or {}).items():
        if tag in DEX_METADATA_KEYS or isinstance(value, (dict, list)):
            continue
        emit(f'/dex/{_pointer(tag)}', *_coerce(value))

    return readings


def in_batches(readings, size: int = MAX_READINGS_PER_BATCH):
    """Yield ``readings`` in batches ``ingest_readings`` will accept.

    One real PH_3 snapshot flattens to roughly 760 readings - about 700 ``dex``
    tags plus the station and pump summaries - while a batch may carry 500. So a
    whole snapshot never fits in one call, and ``ingest_readings`` raises rather
    than truncating.

    This lives here, next to the function that produces the oversized list,
    because every caller of :func:`flatten_snapshot` needs it. Leaving each one
    to slice by hand is how a connector ends up ingesting the first 500 tags of a
    plant and quietly discarding the rest.
    """
    if size < 1:
        raise ValueError('Batch size must be at least one reading.')

    batch: list[Reading] = []
    for reading in readings:
        batch.append(reading)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch


def status_label(code) -> str:
    """Human label for a status code, or the code itself when unrecognised.

    Returning the raw code is deliberate: an unfamiliar code shown as-is invites
    the question, while one mapped to a plausible guess does not.
    """
    return STATUS_CODES.get(code, str(code))
