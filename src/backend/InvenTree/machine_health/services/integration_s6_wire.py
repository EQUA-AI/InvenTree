"""Bounded, lossless recorded-observation encoding without I/O or authority.

Origins are producer claims. Canonical bytes detach mutable values; they do not
prove physical clock, mapping, source completeness, or current actor authority.
"""

import json
import math
from datetime import datetime
from typing import NoReturn

from machine_health.integration_contracts import (
    RecordedTelemetrySample,
    RecordedTelemetrySampleV2,
    TelemetrySourceIdentity,
)

MAX_OBSERVATION_BYTES = 1024 * 1024
MAX_HISTORY_BYTES = 8 * 1024 * 1024
MAX_VALUE_DEPTH = 32
MAX_VALUE_NODES = 20000
_SAMPLE_FIELDS = (
    'quality',
    'quality_origin',
    'unit',
    'unit_origin',
    'value_origin',
    'source_observation_id',
    'source_sequence',
    'time_origin',
    'external_key',
    'observation_id_origin',
    'sequence_origin',
    'source_generation',
)
_SOURCE_FIELDS = ('environment', 'machine_pk', 'source_pk', 'binding_pk')


class SourceSerializationError(Exception):
    """An observation cannot be represented under the bounded source contract."""


def _fail() -> NoReturn:
    raise SourceSerializationError('Recorded observation is unavailable') from None


def _value(value, depth=0, budget=None):
    """Detach plain JSON values without coercion, defaults or unbounded walks."""
    if budget is None:
        budget = [MAX_VALUE_NODES]
    budget[0] -= 1
    if budget[0] < 0 or depth > MAX_VALUE_DEPTH:
        _fail()
    kind = type(value)
    if value is None or kind in (bool, int):
        return value
    if kind is float:
        if not math.isfinite(value):
            _fail()
        return value
    if kind is str:
        if len(value) > MAX_OBSERVATION_BYTES:
            _fail()
        return value
    if kind is list:
        if len(value) > budget[0]:
            _fail()
        return [_value(item, depth + 1, budget) for item in value]
    if kind is dict:
        if len(value) > budget[0]:
            _fail()
        result = {}
        for key, item in value.items():
            if type(key) is not str or len(key) > MAX_OBSERVATION_BYTES:
                _fail()
            result[key] = _value(item, depth + 1, budget)
        return result
    _fail()


def bounded_json(body, *, max_bytes):
    """Encode a validated JSON tree while enforcing the output-byte ceiling."""
    if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_HISTORY_BYTES:
        _fail()
    chunks = []
    size = 0
    for piece in json.JSONEncoder(
        ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':')
    ).iterencode(body):
        data = piece.encode('utf-8')
        size += len(data)
        if size > max_bytes:
            _fail()
        chunks.append(data)
    return b''.join(chunks)


def _instant(value):
    if type(value) is not datetime or value.utcoffset() is None:
        _fail()
    return value.isoformat()


def _project(record):
    if type(record) is RecordedTelemetrySample:
        record = RecordedTelemetrySampleV2(sample=record)
    if type(record) is not RecordedTelemetrySampleV2:
        _fail()
    if (
        type(record.schema_version) is not str
        or record.schema_version != 'aimms.telemetry.recorded.v2'
    ):
        _fail()
    sample = record.sample
    if type(sample) is not RecordedTelemetrySample:
        _fail()
    if (
        type(sample.schema_version) is not str
        or sample.schema_version != 'aimms.telemetry.recorded.v1'
    ):
        _fail()
    source = sample.source
    if type(source) is not TelemetrySourceIdentity:
        _fail()
    TelemetrySourceIdentity.__post_init__(source)
    RecordedTelemetrySample.__post_init__(sample)
    RecordedTelemetrySampleV2.__post_init__(record)
    for field in ('quality', 'unit'):
        if (
            getattr(sample, field) is not None
            and type(getattr(sample, field)) is not str
        ):
            _fail()
    metadata = {name: getattr(sample, name) for name in _SAMPLE_FIELDS}
    for item in metadata.values():
        if type(item) is str and len(item) > MAX_OBSERVATION_BYTES:
            _fail()
    return {
        'schema_version': record.schema_version,
        'sample': {
            'schema_version': sample.schema_version,
            'source': {name: getattr(source, name) for name in _SOURCE_FIELDS},
            'observed_at': _instant(sample.observed_at),
            'value': _value(sample.value),
            **metadata,
        },
        'ingested_at': _instant(record.ingested_at)
        if record.ingested_at is not None
        else None,
        'ingest_time_origin': record.ingest_time_origin,
    }


def encode_observation(record) -> bytes:
    """Capture every v1 field and optional v2 receipt into detached exact bytes.

    A v1 record acquires only the explicit v2 unknown receipt envelope. Supported
    timestamps retain their original offset and precision, without epoch guesses.
    """
    try:
        payload = bounded_json(_project(record), max_bytes=MAX_OBSERVATION_BYTES)
        if bounded_json(_project(record), max_bytes=MAX_OBSERVATION_BYTES) != payload:
            _fail()
        return payload
    except (ValueError, TypeError, OverflowError, RecursionError, UnicodeError):
        _fail()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail()
        result[key] = value
    return result


def _constant(value):
    _fail()


def _datetime(value):
    if type(value) is not str:
        _fail()
    return datetime.fromisoformat(value)


def decode_observation(payload) -> RecordedTelemetrySampleV2:
    """Validate canonical source bytes and reconstruct a newly detached DTO.

    Decoding grants no authority. Unknown, malformed and extra fields are not
    repaired, omitted or accepted as equivalent source observations.
    """
    try:
        if type(payload) is not bytes or not 1 <= len(payload) <= MAX_OBSERVATION_BYTES:
            _fail()
        body = json.loads(payload, object_pairs_hook=_pairs, parse_constant=_constant)
        if type(body) is not dict or set(body) != {
            'schema_version',
            'sample',
            'ingested_at',
            'ingest_time_origin',
        }:
            _fail()
        if (
            type(body['schema_version']) is not str
            or body['schema_version'] != 'aimms.telemetry.recorded.v2'
        ):
            _fail()
        sample = body['sample']
        if type(sample) is not dict or set(sample) != {
            'schema_version',
            'source',
            'observed_at',
            'value',
            *_SAMPLE_FIELDS,
        }:
            _fail()
        if (
            type(sample['schema_version']) is not str
            or sample['schema_version'] != 'aimms.telemetry.recorded.v1'
        ):
            _fail()
        source = sample['source']
        if type(source) is not dict or set(source) != set(_SOURCE_FIELDS):
            _fail()
        record = RecordedTelemetrySampleV2(
            sample=RecordedTelemetrySample(
                source=TelemetrySourceIdentity(**source),
                observed_at=_datetime(sample['observed_at']),
                value=_value(sample['value']),
                **{name: sample[name] for name in _SAMPLE_FIELDS},
            ),
            ingested_at=_datetime(body['ingested_at'])
            if body['ingested_at'] is not None
            else None,
            ingest_time_origin=body['ingest_time_origin'],
        )
        if encode_observation(record) != payload:
            _fail()
        return record
    except (
        ValueError,
        TypeError,
        OverflowError,
        RecursionError,
        UnicodeError,
        KeyError,
    ):
        _fail()


def history_body(adapter, captured, *, expected_context, expected_window):
    """Build a validated synthetic source result, never a complete population."""
    from machine_health.services.integration_s6_adapter import (
        FixtureHistoryAdapter,
        context_key,
        source_key,
        window_key,
    )

    if type(adapter) is not FixtureHistoryAdapter:
        _fail()
    adapter.validate_read(
        captured, expected_context=expected_context, expected_window=expected_window
    )
    context_key(expected_context)
    start, end = window_key(expected_window)
    environment, machine_pk, source_pk, binding_pk = source_key(adapter.source)
    gaps = sorted(
        (gap.window.start_ms, gap.window.end_ms, gap.reason) for gap in captured.gaps
    )
    complete_intervals = []
    position = start
    for gap_start, gap_end, _reason in gaps:
        if position < gap_start:
            complete_intervals.append([position, gap_start])
        position = gap_end
    if position < end:
        complete_intervals.append([position, end])
    return {
        'schema_version': 'aimms.s6.history-source.v1',
        'context': {
            'environment': environment,
            'actor_pk': expected_context.actor_pk,
            'machine_pk': machine_pk,
            'source_pk': source_pk,
            'binding_pk': binding_pk,
            'generation': expected_context.generation,
        },
        'requested_window': [start, end],
        'mapping': adapter.mapping.as_dict(),
        'source_evidence': {
            'kind': 'synthetic_fixture',
            'fingerprint': captured.fixture_fingerprint,
        },
        'observations': [json.loads(payload) for payload in captured.observations],
        'gaps': [{'window': [a, b], 'reason': reason} for a, b, reason in gaps],
        'complete_intervals': complete_intervals,
        'complete': not gaps,
        'population_complete': False,
        'revision_complete': False,
        'telemetry_presentation': 'recorded_history',
        'recorded_telemetry_as_live': False,
        'display_shifted': False,
    }


def encode_history_read(adapter, captured, *, expected_context, expected_window):
    """Encode all admitted provenance and actual coverage within a fixed cap."""
    return bounded_json(
        history_body(
            adapter,
            captured,
            expected_context=expected_context,
            expected_window=expected_window,
        ),
        max_bytes=MAX_HISTORY_BYTES,
    )


def check_history_payload(
    payload, adapter, captured, *, expected_context, expected_window
):
    """Compare exact final bytes with independently rebuilt qualified source data."""
    if type(payload) is not bytes or not 1 <= len(payload) <= MAX_HISTORY_BYTES:
        _fail()
    expected = bounded_json(
        history_body(
            adapter,
            captured,
            expected_context=expected_context,
            expected_window=expected_window,
        ),
        max_bytes=MAX_HISTORY_BYTES,
    )
    if payload != expected:
        _fail()
