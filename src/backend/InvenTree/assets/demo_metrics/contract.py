"""Strict contract adapter for the EQUA demo metrics fixture and resolved mapping.

This is the only place that understands the frozen fixture vocabulary. It
translates fixture semantics into backend semantics and fails closed on
anything it does not recognize:

* fixture ``quality: valid`` -> backend ``good`` (anything else rejected),
* fixture ``due_at`` (an instant) -> application ``due_date`` (a date) in the
  approved reporting timezone, retaining the original instant for provenance,
* fixture priorities P1/P2 -> ``high``, P3 -> ``medium``, P4 -> ``low`` (the
  explicitly approved demo mapping; a different mapping is refused here rather
  than silently honoured),
* fixture external tags -> namespaced backend tags
  ``<dataset>/<session>/<alias>/<signal>`` with uniqueness enforced across
  ``(source, external_key)`` in preflight and again at locked creation,
* fixture clock policy -> synthetic source freshness of 300 seconds,
* fixture ``>=`` threshold reference semantics stay in :mod:`.reference`; the
  backend keeps its strict ``>`` semantics (see ``MachineSignalBinding.classify``
  and the equality tests in ``test_demo_metrics_contract.py``).

The resolved mapping is validated strictly: unknown fields, null identities,
duplicate aliases/targets, client mismatch, unverified synthetic ownership or
hash mismatch all raise :class:`ContractError` before any effect is planned.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from . import reference

#: Fixture quality vocabulary -> backend ``SignalQuality`` values.
QUALITY_TRANSLATION = {'valid': 'good'}

#: Approved demo priority translation (P1/P2 -> high, P3 -> medium, P4 -> low).
PRIORITY_TRANSLATION = {'P1': 'high', 'P2': 'high', 'P3': 'medium', 'P4': 'low'}

#: Fixture state names map to identically named application lifecycle states.
STATE_TRANSLATION = {
    'planned': 'planned',
    'ready': 'ready',
    'in_progress': 'in_progress',
    'on_hold': 'on_hold',
    'verifying': 'verifying',
    'completed': 'completed',
}

#: Synthetic source freshness demanded by the fixture clock policy (seconds).
SYNTHETIC_FRESHNESS_SECONDS = 300

#: The only effect class the synthetic session policy allows.
ALLOWED_EFFECTS = ('database_write',)
DENIED_EFFECTS = (
    'email',
    'industrial_control',
    'webhook',
    'ai_diagnosis',
    'plugin_dispatch',
    'external_publish',
)

#: Tag namespace for session-owned synthetic bindings.
TAG_NAMESPACE = 'equa-demo-metrics-v1'

#: Bounded import sizes (fixture totals are 15 bindings / 12 observations /
#: 9 work orders / 28 coverage intervals / 13 downtime intervals).
MAX_BINDINGS = 64
MAX_OBSERVATIONS = 256
MAX_WORK_ORDERS = 64
MAX_HISTORY_INTERVALS = 512
MAX_INTERVAL_DAYS = 90


class ContractError(Exception):
    """The fixture or mapping violates the reviewed contract."""

    def __init__(self, code: str, message: str):
        """Carry a stable machine-readable code plus a human message."""
        self.code = code
        super().__init__(f'{code}: {message}')


def _reject_unknown(data: dict, allowed: set[str], where: str) -> None:
    unknown = set(data) - allowed
    if unknown:
        raise ContractError(
            'UNKNOWN_FIELD', f'{where} contains unsupported fields: {sorted(unknown)}'
        )


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise ContractError(code, message)


def _dict(value: Any, name: str) -> dict:
    if not isinstance(value, dict):
        raise ContractError('MAPPING_INVALID', f'{name} must be an object')
    return value


def _list(value: Any, name: str) -> list:
    if not isinstance(value, list):
        raise ContractError('MAPPING_INVALID', f'{name} must be a list')
    return value


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ContractError('NOT_FINITE', f'{name} must be a finite number')
    result = float(value)
    if not math.isfinite(result):
        raise ContractError('NOT_FINITE', f'{name} must be a finite number')
    return result


def _instant(value: Any, name: str) -> datetime:
    if not isinstance(value, str):
        raise ContractError('BAD_TIMESTAMP', f'{name} must be an ISO-8601 string')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ContractError(
            'BAD_TIMESTAMP', f'{name} is not a valid timestamp'
        ) from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise ContractError('BAD_TIMESTAMP', f'{name} must be timezone-aware')
    return result.astimezone(timezone.utc)


@dataclass(frozen=True)
class Fixture:
    """A validated fixture plus both of its identity hashes."""

    data: dict
    canonical_sha256: str
    file_sha256: str
    as_of: datetime

    @property
    def dataset_key(self) -> str:
        """Stable dataset identity of the fixture."""
        return self.data['dataset_key']

    @property
    def stale_after_seconds(self) -> int:
        """Freshness budget declared by the fixture clock policy."""
        return int(self.data['clock_policy']['stale_after_seconds'])


def load_fixture(path) -> Fixture:
    """Load, validate and fingerprint a fixture file."""
    path = Path(path)
    try:
        raw = path.read_bytes()
        data = json.loads(raw.decode('utf-8'))
    except (OSError, ValueError) as exc:
        raise ContractError(
            'FIXTURE_UNREADABLE', f'Cannot read fixture: {exc}'
        ) from exc
    if not isinstance(data, dict):
        raise ContractError('FIXTURE_INVALID', 'Fixture root must be an object')
    for number in _walk_numbers(data):
        if not math.isfinite(number):
            raise ContractError('NOT_FINITE', 'Fixture contains a non-finite number')
    try:
        reference.validate(data)
    except ValueError as exc:
        raise ContractError('FIXTURE_INVALID', str(exc)) from exc
    _require(
        data.get('application_adapter_required') is True,
        'FIXTURE_INVALID',
        'Fixture must require the application adapter',
    )
    return Fixture(
        data=data,
        canonical_sha256=reference.content_hash(data),
        file_sha256=reference.file_byte_hash(path),
        as_of=reference.timestamp(data['as_of']),
    )


def _walk_numbers(value: Any):
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        yield float(value)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk_numbers(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_numbers(item)


def namespaced_tag(
    dataset_key: str, session_key: str, alias: str, signal_key: str
) -> str:
    """Return the unique external tag for one session-owned binding."""
    return f'{dataset_key}/{session_key}/{alias}/{signal_key}'


def translate_quality(quality: str) -> str:
    """Translate fixture quality to the backend vocabulary."""
    try:
        return QUALITY_TRANSLATION[quality]
    except KeyError as exc:
        raise ContractError(
            'BAD_QUALITY', f'Unsupported fixture quality {quality!r}'
        ) from exc


def translate_priority(priority: str) -> str:
    """Translate a fixture priority code using the approved demo mapping."""
    try:
        return PRIORITY_TRANSLATION[priority]
    except KeyError as exc:
        raise ContractError(
            'BAD_PRIORITY', f'Unsupported fixture priority {priority!r}'
        ) from exc


def translate_due_date(
    due_at: datetime,
    fixture_as_of: datetime,
    session_anchor: datetime,
    reporting_timezone: str,
) -> tuple[date, str]:
    """Convert a fixture due instant into an application due date.

    The instant is shifted with the fixture offset (so a fixture due three days
    after its anchor is due three days after the session anchor), then reduced
    to a calendar date in the approved reporting timezone. The original instant
    is returned in ISO form for bounded provenance.
    """
    try:
        zone = ZoneInfo(reporting_timezone)
    except Exception as exc:
        raise ContractError(
            'BAD_TIMEZONE', f'Unknown reporting timezone: {reporting_timezone}'
        ) from exc
    offset = due_at - fixture_as_of
    instant = session_anchor + offset
    local = instant.astimezone(zone)
    return local.date(), reference.iso(instant)


def translate_instant(
    fixture_instant: datetime, fixture_as_of: datetime, session_anchor: datetime
) -> datetime:
    """Shift a fixture instant by the session anchor delta."""
    return session_anchor + (fixture_instant - fixture_as_of)


@dataclass(frozen=True)
class ResolvedMapping:
    """A validated, apply-ready resolved target mapping."""

    data: dict
    session_key: str
    reporting_timezone: str
    expires_at: datetime
    expected_counts: dict
    side_effect_policy: tuple[str, ...]
    assignees: dict = field(default_factory=dict)
    control_records: dict = field(default_factory=dict)
    source_scope: dict = field(default_factory=dict)
    history_import_approved: bool = False


TOP_LEVEL_FIELDS = {
    'schema_version',
    'ready_for_apply',
    'warning',
    'dataset_key',
    'session_key',
    'target',
    'locations',
    'machines',
    'state_mapping',
    'priority_mapping',
    'ingestion',
    'approval',
    # Reviewed extension fields for the resolved (non-template) mapping.
    'reporting_timezone',
    'side_effect_policy',
    'expires_at',
    'expected_counts',
    'input_hashes',
    'source_scope',
    'assignees',
    'control_records',
    'history_import_approved',
}

TARGET_FIELDS = {
    'subscription_id',
    'resource_group',
    'app_name',
    'region_reported',
    'environment_resource_id',
    'database_identity_fingerprint',
    'approved_commit_sha',
    'approved_image_digest',
    'demo_owner_identity',
}

MACHINE_FIELDS = {
    'target_machine_id',
    'expected_version',
    'verified_synthetic_ownership',
    'ownership_evidence',
    'create',
    'move',
    'rename',
    'client_code',
}

LOCATION_FIELDS = {'target_location_id', 'expected_version', 'create', 'reparent'}

INGESTION_FIELDS = {
    'adapter_id',
    'verified_signal_identifiers',
    'verified_units',
    'verified_stale_threshold_seconds',
    'external_side_effects_isolated',
}


def validate_mapping(
    data: dict, fixture: Fixture, *, require_ready: bool
) -> ResolvedMapping:
    """Validate a resolved mapping against the fixture; fail closed."""
    if not isinstance(data, dict):
        raise ContractError('MAPPING_INVALID', 'Mapping root must be an object')
    _reject_unknown(data, TOP_LEVEL_FIELDS, 'mapping')
    _require(
        data.get('schema_version') == 'equa.demo-target-mapping/1',
        'MAPPING_INVALID',
        'Unsupported mapping schema',
    )
    _require(
        data.get('dataset_key') == fixture.dataset_key,
        'MAPPING_INVALID',
        'Mapping dataset_key does not match the fixture',
    )
    session_key = data.get('session_key')
    _require(
        isinstance(session_key, str) and session_key.strip() != '',
        'MAPPING_INVALID',
        'Mapping session_key must be resolved',
    )
    _require(
        all(ch.isalnum() or ch in '-_.' for ch in session_key),
        'MAPPING_INVALID',
        'Mapping session_key must be a simple slug',
    )

    target = _dict(data.get('target'), 'mapping.target')
    _reject_unknown(target, TARGET_FIELDS, 'mapping.target')
    _require(
        isinstance(target.get('resource_group'), str) and bool(target.get('app_name')),
        'MAPPING_INVALID',
        'Mapping target must name the exact target',
    )

    machines = _dict(data.get('machines'), 'mapping.machines')
    locations = _dict(data.get('locations'), 'mapping.locations')
    fixture_machines = {m['key'] for m in fixture.data['machines']}
    fixture_locations = {n['key'] for n in fixture.data['locations']}
    _require(
        set(machines) == fixture_machines,
        'MAPPING_INVALID',
        'Mapping machine aliases must cover exactly the fixture aliases',
    )
    _require(
        set(locations) == fixture_locations,
        'MAPPING_INVALID',
        'Mapping location aliases must cover exactly the fixture aliases',
    )

    seen_targets: set[int] = set()
    for alias, entry in sorted(machines.items()):
        _require(
            isinstance(entry, dict),
            'MAPPING_INVALID',
            f'machines.{alias} must be an object',
        )
        _reject_unknown(entry, MACHINE_FIELDS, f'mapping.machines.{alias}')
        machine_id = entry.get('target_machine_id')
        _require(
            isinstance(machine_id, int)
            and not isinstance(machine_id, bool)
            and machine_id > 0,
            'UNRESOLVED_MAPPING',
            f'machines.{alias}.target_machine_id must be resolved',
        )
        _require(
            machine_id not in seen_targets,
            'DUPLICATE_TARGET',
            'Two aliases share one machine',
        )
        seen_targets.add(machine_id)
        _require(
            entry.get('verified_synthetic_ownership') is True,
            'OWNERSHIP_UNPROVEN',
            f'machines.{alias} lacks verified synthetic ownership evidence',
        )
        _require(
            entry.get('create') is False
            and entry.get('move') is False
            and entry.get('rename') is False,
            'MUTATING_MAPPING',
            f'machines.{alias} must not create, move or rename records',
        )
        _require(
            isinstance(entry.get('client_code'), str) and entry['client_code'],
            'UNRESOLVED_MAPPING',
            f'machines.{alias}.client_code must be resolved',
        )
        evidence = _dict(
            entry.get('ownership_evidence'), f'machines.{alias}.ownership_evidence'
        )
        method = evidence.get('method')
        _require(
            method in OWNERSHIP_EVIDENCE_METHODS,
            'OWNERSHIP_UNPROVEN',
            f'machines.{alias}.ownership_evidence.method must name a trusted '
            'loader ownership rule',
        )
        if method == 'managed_demo_part':
            ipns = evidence.get('part_ipns')
            _require(
                isinstance(ipns, list)
                and bool(ipns)
                and all(isinstance(ipn, str) and ipn.strip() for ipn in ipns),
                'OWNERSHIP_UNPROVEN',
                f'machines.{alias}.ownership_evidence.part_ipns must name '
                'managed demo parts',
            )
        else:
            identity = _dict(
                evidence.get('identity'),
                f'machines.{alias}.ownership_evidence.identity',
            )
            _require(
                set(identity) == {'manufacturer', 'model', 'serial'}
                and all(isinstance(value, str) for value in identity.values()),
                'OWNERSHIP_UNPROVEN',
                f'machines.{alias}.ownership_evidence.identity must record '
                'manufacturer, model and serial',
            )

    for alias, entry in sorted(locations.items()):
        _require(
            isinstance(entry, dict),
            'MAPPING_INVALID',
            f'locations.{alias} must be an object',
        )
        _reject_unknown(entry, LOCATION_FIELDS, f'mapping.locations.{alias}')
        location_id = entry.get('target_location_id')
        _require(
            isinstance(location_id, int)
            and not isinstance(location_id, bool)
            and location_id > 0,
            'UNRESOLVED_MAPPING',
            f'locations.{alias}.target_location_id must be resolved',
        )
        _require(
            entry.get('create') is False and entry.get('reparent') is False,
            'MUTATING_MAPPING',
            f'locations.{alias} must not create or reparent records',
        )

    priority_mapping = data.get('priority_mapping')
    _require(
        priority_mapping == PRIORITY_TRANSLATION,
        'MAPPING_INVALID',
        'priority_mapping must be the approved demo translation',
    )
    state_mapping = data.get('state_mapping')
    _require(
        state_mapping == STATE_TRANSLATION,
        'MAPPING_INVALID',
        'state_mapping must be the identity translation of lifecycle states',
    )

    ingestion = _dict(data.get('ingestion'), 'mapping.ingestion')
    _reject_unknown(ingestion, INGESTION_FIELDS, 'mapping.ingestion')
    _require(
        ingestion.get('verified_stale_threshold_seconds')
        == fixture.stale_after_seconds,
        'MAPPING_INVALID',
        'Synthetic source freshness must match the fixture clock policy',
    )
    _require(
        ingestion.get('external_side_effects_isolated') is True,
        'EFFECTS_NOT_ISOLATED',
        'Mapping must declare external side effects isolated',
    )
    for signal in fixture.data['signal_definitions']:
        _require(
            ingestion.get('verified_units', {}).get(signal['key']) == signal['unit'],
            'MAPPING_INVALID',
            f'verified_units must confirm unit for {signal["key"]}',
        )

    input_hashes = _dict(data.get('input_hashes'), 'input_hashes')
    _require(
        input_hashes.get('fixture_canonical_sha256') == fixture.canonical_sha256,
        'HASH_MISMATCH',
        'input_hashes.fixture_canonical_sha256 does not match the fixture',
    )
    _require(
        input_hashes.get('fixture_file_sha256') == fixture.file_sha256,
        'HASH_MISMATCH',
        'input_hashes.fixture_file_sha256 does not match the fixture file',
    )

    counts = _dict(data.get('expected_counts'), 'expected_counts')
    derived = expected_counts(fixture)
    _require(
        counts == derived,
        'MAPPING_INVALID',
        'expected_counts must match the fixture totals',
    )

    policy = _list(data.get('side_effect_policy'), 'side_effect_policy')
    _require(
        tuple(policy) == ALLOWED_EFFECTS,
        'EFFECTS_NOT_ISOLATED',
        'side_effect_policy must allow exactly the database_write effect class',
    )

    expires_at = _instant(data.get('expires_at'), 'expires_at')
    source_scope = _dict(data.get('source_scope'), 'source_scope')
    for source in fixture.data['sources']:
        scope = _dict(source_scope.get(source['key']), f'source_scope.{source["key"]}')
        client_code = scope.get('client_code')
        _require(
            isinstance(client_code, str) and bool(client_code),
            'UNRESOLVED_MAPPING',
            f'source_scope must bind {source["key"]} to a security scope',
        )
        site_key = scope.get('site_key')
        _require(
            isinstance(site_key, str) and bool(site_key),
            'UNRESOLVED_MAPPING',
            f'source_scope must bind {source["key"]} to an explicit security '
            'site key (physical aliases are never security scope)',
        )
        _require(
            site_key not in fixture_locations and site_key not in fixture_machines,
            'SOURCE_SCOPE_MISMATCH',
            f'source_scope.{source["key"]}.site_key must not be a fixture '
            'physical location/machine alias reused as a security scope',
        )
        served = {
            binding['machine_key']
            for binding in fixture.data['bindings']
            if binding['source_key'] == source['key']
        }
        for machine_key in sorted(served):
            _require(
                machines[machine_key].get('client_code') == client_code,
                'SOURCE_SCOPE_MISMATCH',
                f'source_scope.{source["key"]}.client_code does not match the '
                f'mapped client of machine {machine_key}',
            )

    history_import_approved = data.get('history_import_approved', False)
    _require(
        isinstance(history_import_approved, bool),
        'MAPPING_INVALID',
        'history_import_approved must be a boolean',
    )

    assignees = _dict(data.get('assignees') or {}, 'assignees')
    control_records = _dict(data.get('control_records') or {}, 'control_records')
    work_order_keys = {order['key'] for order in fixture.data['work_orders']}
    _require(
        set(assignees) <= work_order_keys,
        'MAPPING_INVALID',
        'assignees must name fixture work orders only',
    )
    _require(
        set(control_records) <= work_order_keys,
        'MAPPING_INVALID',
        'control_records must name fixture work orders only',
    )
    for key, entry in control_records.items():
        _require(
            isinstance(entry, dict)
            and entry.get('mode') in {'synthetic_import', 'reference_existing'},
            'MAPPING_INVALID',
            f'control_records.{key} must declare a supported control mode',
        )

    if require_ready:
        _require(
            data.get('ready_for_apply') is True,
            'MAPPING_NOT_READY',
            'Mapping is not marked ready_for_apply',
        )
        target_identity = target.get('database_identity_fingerprint')
        _require(
            isinstance(target_identity, str) and target_identity,
            'UNRESOLVED_MAPPING',
            'target.database_identity_fingerprint must be resolved',
        )
        _require(
            isinstance(target.get('demo_owner_identity'), str)
            and target['demo_owner_identity'],
            'UNRESOLVED_MAPPING',
            'target.demo_owner_identity must be resolved',
        )

    return ResolvedMapping(
        data=data,
        session_key=session_key,
        reporting_timezone=data.get('reporting_timezone') or 'UTC',
        expires_at=expires_at,
        expected_counts=derived,
        side_effect_policy=tuple(policy),
        assignees=assignees,
        control_records=control_records,
        source_scope=source_scope,
        history_import_approved=history_import_approved,
    )


def expected_counts(fixture: Fixture) -> dict:
    """Derive the reviewed cohort expectations from the fixture itself."""
    data = fixture.data
    configured = [m for m in data['machines'] if m['configured']]
    open_orders = [
        order
        for order in data['work_orders']
        if order['requested_state'] in reference.OPEN_STATES
    ]
    return {
        'bindings': len(data['bindings']),
        'observations': len(data['readings']),
        'work_orders': len(data['work_orders']),
        'open_work_orders': len(open_orders),
        'completed_controls': len(data['work_orders']) - len(open_orders),
        'configured_machines': len(configured),
    }


@dataclass(frozen=True)
class TranslatedReading:
    """One fixture reading translated into backend ingestion vocabulary."""

    item_key: str
    alias: str
    machine_alias: str
    source_alias: str
    external_key: str
    value: float
    observed_at: datetime
    quality: str
    sequence: int


def translate_readings(
    fixture: Fixture, mapping: ResolvedMapping, session_anchor: datetime
) -> list[TranslatedReading]:
    """Translate every fixture reading for one session anchor.

    Timestamps are shifted from the fixture anchor to the session anchor; the
    session anchor must be fresh relative to ``now`` at apply time (enforced by
    the apply service) so no fixture anchor is ever presented as the live clock.
    """
    data = fixture.data
    bindings = {(b['machine_key'], b['signal_key']): b for b in data['bindings']}
    signals = {s['key']: s for s in data['signal_definitions']}
    translated: list[TranslatedReading] = []
    seen_tags: set[str] = set()
    for reading in sorted(data['readings'], key=lambda r: r['key']):
        _require(
            reading.get('synthetic') is True,
            'NOT_SYNTHETIC',
            'Reading must be synthetic',
        )
        pair = (reading['machine_key'], reading['signal_key'])
        binding = bindings.get(pair)
        _require(
            binding is not None,
            'UNBOUND_READING',
            f'Reading {reading["key"]} has no binding',
        )
        _require(
            binding['source_key'] == reading['source_key'],
            'SOURCE_MISMATCH',
            f'Reading {reading["key"]} source does not match its binding',
        )
        signal = signals[reading['signal_key']]
        _require(
            reading['unit'] == signal['unit'],
            'UNIT_MISMATCH',
            f'Reading {reading["key"]} unit mismatch',
        )
        value = _finite_number(reading['value'], f'reading {reading["key"]} value')
        tag = namespaced_tag(
            fixture.dataset_key,
            mapping.session_key,
            reading['machine_key'],
            reading['signal_key'],
        )
        _require(tag not in seen_tags, 'DUPLICATE_TAG', f'Duplicate external tag {tag}')
        seen_tags.add(tag)
        translated.append(
            TranslatedReading(
                item_key=reading['key'],
                alias=tag,
                machine_alias=reading['machine_key'],
                source_alias=reading['source_key'],
                external_key=tag,
                value=value,
                observed_at=translate_instant(
                    reference.timestamp(reading['observed_at']),
                    fixture.as_of,
                    session_anchor,
                ),
                quality=translate_quality(reading['quality']),
                sequence=len(translated) + 1,
            )
        )
    _require(
        len(translated) <= MAX_OBSERVATIONS,
        'BATCH_TOO_LARGE',
        f'At most {MAX_OBSERVATIONS} observations per session',
    )
    return translated


#: Accepted bases for synthetic-ownership evidence. A bare mapping boolean,
#: a name, a generic tag or a machine description is never evidence: the
#: evidence must be verifiable against the database with the existing demo
#: loaders' ownership rules (managed demo-part marker or demo manifest
#: identity), and the apply service re-verifies it under the machine row lock.
OWNERSHIP_EVIDENCE_METHODS = {'managed_demo_part', 'demo_manifest_identity'}
