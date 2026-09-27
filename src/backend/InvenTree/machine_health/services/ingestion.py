"""Normalized ingestion of machine signal readings.

Every connector - polling adapter or signed webhook - funnels through
:func:`ingest_readings`. That single entry point is what makes the guarantees
below true regardless of which industrial platform the data came from:

* the caller never chooses which database row to write. A reading names an
  opaque external tag; the binding table decides whether that tag is mapped to a
  machine at all, so a client cannot address an arbitrary signal;
* replays and out-of-order deliveries are dropped, using the source sequence when
  the platform provides one and the observation time otherwise;
* payloads are bounded before they are stored, and only a hash of the raw
  provider body is kept.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from assets.health_models import (
    HealthSource,
    MachineSignalBinding,
    MachineSignalState,
    SignalQuality,
)
from assets.models import AssetMachine
from machine_health.connectors.base import PUMPHOUSE_CONNECTOR_TYPES

logger = logging.getLogger('inventree')

#: One request may not carry an unbounded batch. Connectors page instead.
MAX_READINGS_PER_BATCH = 500

#: A normalized value is a scalar plus small context, never a nested document.
MAX_VALUE_BYTES = 2048

#: How far ahead of the server clock an observation may claim to be before it is
#: rejected as unusable. Small skew is normal; hours are a misconfigured clock.
MAX_CLOCK_SKEW_SECONDS = 300


class IngestionError(Exception):
    """The submitted batch is malformed or exceeds a bound."""

    code = 'INGESTION_INVALID'


@dataclass
class IngestResult:
    """Outcome of one ingestion batch."""

    accepted: int = 0
    unmapped: int = 0
    replayed: int = 0
    rejected: int = 0
    machine_ids: set[int] = field(default_factory=set)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        """Serialize for an API response."""
        return {
            'accepted': self.accepted,
            'unmapped': self.unmapped,
            'replayed': self.replayed,
            'rejected': self.rejected,
            'warnings': list(self.warnings),
        }


def coerce_datetime(value):
    """Return a datetime matching the project's ``USE_TZ`` convention.

    Connectors send ISO-8601 with an offset, but the deployment (and the test
    runner) may store naive local times. Normalizing here keeps every downstream
    comparison - freshness, replay ordering, skew - on one side of that line.
    """
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError as exc:
            raise IngestionError(f'{value!r} is not an ISO-8601 datetime.') from exc
    if not isinstance(value, datetime):
        raise IngestionError('Expected an ISO-8601 datetime.')

    if settings.USE_TZ:
        return timezone.make_aware(value) if timezone.is_naive(value) else value
    return timezone.make_naive(value) if timezone.is_aware(value) else value


def _payload_hash(payload) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(',', ':'), default=str).encode()
    ).hexdigest()


def _normalize_value(raw, binding: MachineSignalBinding):
    """Apply the binding transform and bound what will be stored."""
    value = raw
    transform = binding.transform or {}

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        scale = transform.get('scale')
        offset = transform.get('offset')
        if isinstance(scale, (int, float)) and not isinstance(scale, bool):
            value = value * scale
        if isinstance(offset, (int, float)) and not isinstance(offset, bool):
            value = value + offset

    stored = {'value': value, 'unit': binding.unit}
    encoded = json.dumps(stored, default=str)
    if len(encoded.encode()) > MAX_VALUE_BYTES:
        raise IngestionError(
            f'Normalized value for {binding.external_key!r} exceeds '
            f'{MAX_VALUE_BYTES} bytes.'
        )
    return stored


def _is_replay(state: MachineSignalState | None, observed_at, sequence) -> bool:
    """Whether an update is older than or identical to what is already stored."""
    if state is None:
        return False

    if sequence is not None and state.source_sequence is not None:
        return sequence <= state.source_sequence

    return observed_at <= state.observed_at


def _parse_reading(entry, index: int):
    """Validate one reading's shape without touching the database."""
    if not isinstance(entry, dict):
        raise IngestionError(f'Reading {index} must be an object.')

    external_key = entry.get('external_key') or entry.get('tag')
    if not isinstance(external_key, str) or not external_key.strip():
        raise IngestionError(f'Reading {index} needs an external_key.')

    if 'value' not in entry:
        raise IngestionError(f'Reading {index} needs a value.')

    observed_at = entry.get('observed_at')
    if observed_at is None:
        raise IngestionError(f'Reading {index} needs an observed_at timestamp.')
    observed_at = coerce_datetime(observed_at)

    quality = entry.get('quality') or SignalQuality.GOOD
    if quality not in SignalQuality.values:
        raise IngestionError(f'Reading {index} has an unknown quality {quality!r}.')

    sequence = entry.get('sequence')
    if sequence is not None and (
        isinstance(sequence, bool) or not isinstance(sequence, int)
    ):
        raise IngestionError(f'Reading {index} sequence must be an integer.')

    return {
        'external_key': external_key.strip(),
        'value': entry.get('value'),
        'observed_at': observed_at,
        'quality': quality,
        'sequence': sequence,
    }


@transaction.atomic
def ingest_readings(
    source: HealthSource, readings, *, now=None, station=None
) -> IngestResult:
    """Apply a batch of normalized readings from one source.

    Unmapped tags are counted and dropped rather than auto-creating bindings: a
    source must not be able to invent machine signals, and an operator mapping a
    tag is a deliberate configuration act.
    """
    if not isinstance(readings, list):
        raise IngestionError('readings must be a list.')
    if len(readings) > MAX_READINGS_PER_BATCH:
        raise IngestionError(
            f'A batch may carry at most {MAX_READINGS_PER_BATCH} readings.'
        )

    now = now or timezone.now()
    result = IngestResult()

    try:
        parsed = [_parse_reading(entry, index) for index, entry in enumerate(readings)]
    except (TypeError, ValueError) as exc:
        raise IngestionError(f'Malformed reading: {exc}') from exc

    if not parsed:
        return result

    candidates = MachineSignalBinding.objects.select_related('machine').filter(
        source=source,
        active=True,
        external_key__in=[item['external_key'] for item in parsed],
    )
    if source.connector_type in PUMPHOUSE_CONNECTOR_TYPES and station is None:
        raise IngestionError(
            'Cosmos ingestion requires an explicit registered station.'
        )
    if station is not None:
        if station.asset_type != 'pumphouse' or not station.pk or not station.client_id:
            raise IngestionError('Ingestion scope must be a registered station.')
        # A review change must not race a cached binding into recreating state
        # after approval was revoked. Dictionary saves use the same station lock.
        AssetMachine.objects.select_for_update().get(pk=station.pk)
        candidates = candidates.filter(
            Q(machine=station) | Q(machine__parent=station),
            machine__client_id=station.client_id,
            machine__active=True,
        )
    if station is not None:
        candidates = candidates.filter(
            Q(dictionary_point__isnull=True)
            | Q(dictionary_point__station=station, dictionary_point__status='approved')
        )
    bindings = {}
    for binding in candidates:
        if binding.external_key in bindings:
            raise IngestionError('Ambiguous signal binding within ingestion scope.')
        bindings[binding.external_key] = binding

    # One locking read for the whole batch, not one per reading.
    #
    # The per-reading version issued a SELECT ... FOR UPDATE and then an
    # INSERT or UPDATE for every single reading: two round trips each, which on
    # a real pumphouse snapshot of ~700 readings is ~1,400 of them and dominates
    # ingestion entirely. The rows taken are exactly the same ones; they are
    # simply taken once.
    #
    # Ordered by primary key so that concurrent batches acquire rows in the same
    # order. Without that, two overlapping batches can each hold a row the other
    # needs and deadlock - a risk the per-reading form did not have, because it
    # only ever held one row at a time.
    states = {
        state.binding_id: state
        for state in MachineSignalState.objects
        .select_for_update()
        .filter(binding__in=list(bindings.values()))
        .order_by('pk')
    }
    fresh: dict[int, MachineSignalState] = {}
    changed: dict[int, MachineSignalState] = {}

    for item in parsed:
        binding = bindings.get(item['external_key'])
        if binding is None:
            result.unmapped += 1
            continue

        skew = (item['observed_at'] - now).total_seconds()
        if skew > MAX_CLOCK_SKEW_SECONDS:
            result.rejected += 1
            result.warnings.append(
                f'{item["external_key"]}: observation is {int(skew)}s in the future'
            )
            continue

        state = states.get(binding.pk)

        if _is_replay(state, item['observed_at'], item['sequence']):
            result.replayed += 1
            continue

        values = {
            'value': _normalize_value(item['value'], binding),
            'observed_at': item['observed_at'],
            'received_at': now,
            'quality': item['quality'],
            'source_sequence': item['sequence'],
            'payload_hash': _payload_hash(item['value']),
        }

        if state is None:
            state = MachineSignalState(binding=binding, **values)
            # Registered before the write, so a second reading for the same
            # binding later in this batch is compared against this one and is
            # dropped as a replay if it is older - which is what the
            # read-modify-write loop did when each reading saved immediately.
            states[binding.pk] = state
            fresh[binding.pk] = state
        else:
            for name, value in values.items():
                setattr(state, name, value)
            # bulk_update does not run pre_save, so auto_now never fires here.
            state.updated_at = now
            if binding.pk not in fresh:
                changed[binding.pk] = state

        result.accepted += 1
        result.machine_ids.add(binding.machine_id)

    if fresh:
        MachineSignalState.objects.bulk_create(
            list(fresh.values()), batch_size=MAX_READINGS_PER_BATCH
        )
    if changed:
        MachineSignalState.objects.bulk_update(
            list(changed.values()),
            [
                'value',
                'observed_at',
                'received_at',
                'quality',
                'source_sequence',
                'payload_hash',
                'updated_at',
            ],
            batch_size=MAX_READINGS_PER_BATCH,
        )

    source.last_success_at = now
    source.save(update_fields=['last_success_at', 'updated_at'])

    logger.info(
        'machine_health.ingest source=%s accepted=%s unmapped=%s replayed=%s '
        'rejected=%s',
        source.pk,
        result.accepted,
        result.unmapped,
        result.replayed,
        result.rejected,
    )

    return result


def record_source_error(
    source: HealthSource, code: str, *, now=None, checkpoint=None
) -> None:
    """Record a redacted connector failure against the source.

    Only a short classification is stored. Provider messages can carry endpoints,
    tag names and occasionally credentials, none of which belong in a row that
    surfaces in the Health blade.
    """
    if checkpoint is not None and checkpoint.source_id != source.pk:
        raise IngestionError('Checkpoint does not belong to this source.')
    target = checkpoint if checkpoint is not None else source
    target.last_error_at = now or timezone.now()
    target.last_error_code = (code or 'ERROR')[: 32 if checkpoint is not None else 64]
    target.save(update_fields=['last_error_at', 'last_error_code', 'updated_at'])
