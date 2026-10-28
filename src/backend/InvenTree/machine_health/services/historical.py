"""Explicit, read-only access to reviewed mappings on an original Cosmos source.

Historical selection never activates a source or a binding, consults the current
signal cache, or changes the normal connector's checkpoint requirements.
"""

from __future__ import annotations

import time

from django.db.models import F, Q
from django.http import Http404

from assets.activation import configured_stations, point_hash
from assets.health_models import (
    HealthSource,
    HealthState,
    MachineSignalBinding,
    SignalQuality,
)
from machine_health.connectors.cosmos_pumphouse import (
    CosmosPumphouseConnector,
    _classify,
    bucket_of,
    to_epoch_ms,
)
from machine_health.connectors.pumphouse_payload import flatten_snapshot
from machine_health.services.ingestion import (
    IngestionError,
    _normalize_value,
    coerce_datetime,
)
from machine_health.services.summary import _component

HISTORICAL_READ_SECONDS = 60


def historical_station(machine):
    """Resolve the same-client registered station, failing closed."""
    station = machine if machine.asset_type == 'pumphouse' else machine.parent
    if (
        station is None
        or station.asset_type != 'pumphouse'
        or not machine.active
        or not station.active
        or not machine.client_id
        or station.client_id != machine.client_id
        or not station.client.active
    ):
        raise Http404
    return station


def _validate_source(machine, source):
    station = historical_station(machine)
    if (
        not source.active
        or source.client_id != machine.client_id
        or source.connector_type != CosmosPumphouseConnector.key
        or not station.source_entity_uuid
        or str(station.source_entity_uuid) not in configured_stations(source)
    ):
        raise Http404
    return station


def historical_bindings(machine, source):
    """Read only unchanged, approved mappings within the machine/parent scope."""
    station = _validate_source(machine, source)
    scope = Q(machine=machine)
    if machine.pk == station.pk:
        scope |= Q(machine__parent=station)
    else:
        scope |= Q(machine=station)
    bindings = (
        MachineSignalBinding.objects
        .select_related('source', 'machine', 'dictionary_point__component')
        .filter(
            scope,
            source=source,
            machine__client_id=machine.client_id,
            machine__active=True,
            dictionary_point__station=station,
            dictionary_point__machine_id=F('machine_id'),
            dictionary_point__path=F('external_key'),
            dictionary_point__unit=F('unit'),
            dictionary_point__status='approved',
            dictionary_point__unit_status__in=['verified', 'unitless'],
        )
        .exclude(dictionary_point__data_type='unknown')
    )
    return [
        binding
        for binding in bindings
        if binding.dictionary_hash
        and binding.dictionary_hash == point_hash(binding.dictionary_point)
    ]


def resolve_historical_source(machine, raw_id):
    """Resolve an opt-in source after the API authorizes its parent machine."""
    if raw_id is None:
        return None
    try:
        source_id = int(raw_id)
    except (TypeError, ValueError):
        raise Http404 from None
    if source_id < 1:
        raise Http404
    source = HealthSource.objects.filter(
        pk=source_id, active=True, client_id=machine.client_id
    ).first()
    if source is None or not historical_bindings(machine, source):
        raise Http404
    return source


def get_historical_connector(source, *, machine):
    """Build the original Cosmos adapter without changing live activation."""
    station = _validate_source(machine, source)
    return CosmosPumphouseConnector(
        source,
        station_uuid=str(station.source_entity_uuid),
        deadline=time.monotonic() + HISTORICAL_READ_SECONDS,
    )


def historical_range(machine, source):
    """Return recorded source-clock edges, normalized to project awareness."""
    station = _validate_source(machine, source)
    entry = ((source.config or {}).get('data_ranges') or {}).get(
        str(station.source_entity_uuid)
    ) or {}
    try:
        start, end = (
            coerce_datetime(entry.get('from')),
            coerce_datetime(entry.get('to')),
        )
    except IngestionError:
        return None
    return (start, end) if start < end else None


def historical_metadata(machine, source):
    """Disclose original source identity and dates without connection settings."""
    span = historical_range(machine, source)
    return {
        'history_source': source.pk,
        'source_id': source.pk,
        'source_name': source.name,
        'source_from': span[0].isoformat() if span else None,
        'source_to': span[1].isoformat() if span else None,
        'recorded_telemetry_as_live': False,
        'telemetry_presentation': 'recorded_history',
        'display_shifted': False,
        'display_shift_seconds': 0,
    }


def historical_signal_rows(machine, source):
    """Project one real latest available snapshot without persisting state."""
    bindings = [
        binding
        for binding in historical_bindings(machine, source)
        if binding.machine_id == machine.pk
    ]
    metadata = historical_metadata(machine, source)
    span = historical_range(machine, source)
    latest = {}
    error = ''
    if bindings and span:
        connector = get_historical_connector(source, machine=machine)
        try:
            document = connector.latest_document(
                connector.station, bucket_of(to_epoch_ms(span[1]))
            )
            keys = {binding.external_key for binding in bindings}
            for reading in flatten_snapshot(document) if document else []:
                observed = coerce_datetime(reading.observed_at)
                if reading.external_key in keys and span[0] <= observed <= span[1]:
                    latest[reading.external_key] = reading
        except Exception as exc:
            error = _classify(exc)
        finally:
            connector.close()
    elif not span:
        error = 'NOT_DISCOVERED'

    rows = []
    for binding in bindings:
        reading = latest.get(binding.external_key)
        observed = coerce_datetime(reading.observed_at) if reading else None
        age = (span[1] - observed).total_seconds() if observed and span else None
        stale = age is None or age < 0 or age > source.freshness_threshold_seconds
        quality = reading.quality if reading else SignalQuality.UNKNOWN
        value = _normalize_value(reading.value, binding)['value'] if reading else None
        rows.append({
            **metadata,
            'active': binding.active,
            'critical_designation': None,
            'binding_id': binding.pk,
            'source_type': source.source_type,
            'external_key': binding.external_key,
            'component': _component(binding),
            'display_name': binding.display_name,
            'signal_kind': binding.signal_kind,
            'unit': binding.unit,
            'value': value,
            'observed_at': observed,
            'received_at': None,
            'quality': quality,
            'stale': stale,
            'freshness_threshold_seconds': source.freshness_threshold_seconds,
            'state': binding.classify(value)
            if not stale and quality == SignalQuality.GOOD
            else HealthState.UNKNOWN,
            'limits': {
                name: getattr(binding, name)
                for name in (
                    'normal_min',
                    'normal_max',
                    'warn_min',
                    'warn_max',
                    'critical_min',
                    'critical_max',
                )
            },
            'test_alarms': [],
        })
    snapshot_at = max(
        (row['observed_at'] for row in rows if row['observed_at']), default=None
    )
    return rows, {
        **metadata,
        'snapshot_at': snapshot_at.isoformat() if snapshot_at else None,
        'read_error_code': error,
        'freshness_reference': 'source_to',
    }
