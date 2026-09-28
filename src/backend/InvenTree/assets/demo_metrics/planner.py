"""Canonical plan construction for the EQUA demo metrics commands.

The plan is a local/protected artifact: ``plan_demo_metrics`` writes it and
never touches the database; ``apply_demo_metrics`` requires it. The canonical
body excludes its own hash and the approval envelope, is sorted
deterministically, rejects non-finite numbers and unknown fields, and is
hashed with SHA-256 over canonical JSON. The approval hash establishes
integrity, not authorization: actor authority is checked separately against
the database actor, never against a user-supplied ``approved_by`` string.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from django.utils import timezone

from . import contract, fingerprint, reference

PLAN_VERSION = 'equa.demo-metrics-plan/1'

#: Legal lifecycle edges used to reach each requested fixture state.
TRANSITION_PATHS = {
    'planned': ['planned'],
    'ready': ['planned', 'ready'],
    'in_progress': ['planned', 'ready', 'in_progress'],
    'on_hold': ['planned', 'ready', 'in_progress', 'on_hold'],
    'verifying': ['planned', 'ready', 'in_progress', 'verifying'],
    'completed': ['synthetic_history_import'],
}


class PlanError(Exception):
    """The plan artifact is malformed or does not match its inputs."""

    def __init__(self, code: str, message: str):
        """Carry a stable machine-readable code plus a human message."""
        self.code = code
        self.message = message
        super().__init__(f'{code}: {message}')


def canonical_hash(value) -> str:
    """SHA-256 over canonical JSON (sorted keys, no NaN)."""
    body = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(body.encode('utf-8')).hexdigest()


def plan_body(plan: dict) -> dict:
    """Return the hashable canonical body (no hash, no approval envelope)."""
    return {
        key: value
        for key, value in plan.items()
        if key not in ('plan_hash', 'approval')
    }


def plan_hash(plan: dict) -> str:
    """Hash of the canonical body."""
    return canonical_hash(plan_body(plan))


def sign_plan(plan: dict) -> dict:
    """Return a copy of ``plan`` carrying its computed hash and empty approval."""
    signed = dict(plan)
    signed['plan_hash'] = plan_hash(plan)
    signed.setdefault('approval', {'plan_sha256': None, 'approved_at': None})
    return signed


#: Conflict codes meaning the actor's current scope does not cover a mapped
#: target (or the scope is unresolved). Such a plan is never emitted.
SCOPE_CONFLICT_CODES = frozenset({'OUT_OF_SCOPE', 'SCOPE_UNRESOLVED'})


def write_plan(path, plan: dict) -> str:
    """Write the plan artifact and return its hash. Refuses to overwrite.

    A scope-denied plan produces no artifact at all: when any mapped target
    falls outside the actor's current scope (or the scope is unresolved), the
    write is refused here in the service, before anything reaches disk.
    """
    path = Path(path)
    conflicts = ((plan.get('effects') or {}).get('conflict')) or []
    denied = sorted({item.get('code') for item in conflicts} & SCOPE_CONFLICT_CODES)
    if denied:
        raise PlanError(
            'ACTOR_SCOPE', f'Actor scope does not cover every planned target: {denied}'
        )
    signed = sign_plan(plan)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(signed, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write('\n')
    return signed['plan_hash']


PLAN_FIELDS = {
    'plan_version',
    'dataset_key',
    'session_key',
    'target',
    'runtime_fingerprint',
    'schema',
    'fixture',
    'mapping_sha256',
    'actor',
    'scope',
    'effects',
    'transition_sequence',
    'cohort',
    'time_policy',
    'side_effect_policy',
    'expected_metrics',
    'expires_at',
    'include_history',
}


def load_plan(path) -> dict:
    """Load a plan artifact, strictly; the stored hash must match its body."""
    path = Path(path)
    try:
        plan = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise PlanError('PLAN_UNREADABLE', f'Cannot read plan: {exc}') from exc
    if not isinstance(plan, dict):
        raise PlanError('PLAN_INVALID', 'Plan root must be an object')
    unknown = set(plan) - PLAN_FIELDS - {'plan_hash', 'approval'}
    if unknown:
        raise PlanError(
            'PLAN_INVALID', f'Plan contains unsupported fields: {sorted(unknown)}'
        )
    missing = PLAN_FIELDS - set(plan)
    if missing:
        raise PlanError('PLAN_INVALID', f'Plan is missing fields: {sorted(missing)}')
    if plan.get('plan_version') != PLAN_VERSION:
        raise PlanError('PLAN_INVALID', 'Unsupported plan version')
    for number in _walk_numbers(plan):
        if number != number or number in (float('inf'), float('-inf')):
            raise PlanError('PLAN_INVALID', 'Plan contains a non-finite number')
    stored = plan.get('plan_hash')
    computed = plan_hash(plan)
    if stored != computed:
        raise PlanError('HASH_MISMATCH', 'Plan hash does not match the plan body')
    return plan


def _walk_numbers(value):
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


@dataclass(frozen=True)
class TargetResolution:
    """One resolved borrowed record with its observed identity."""

    alias: str
    target_id: int
    client_code: str
    placement_id: int | None
    placement_version: int
    in_actor_scope: bool
    ownership_evidence: dict = field(default_factory=dict)


class OwnershipUnverifiedError(Exception):
    """Synthetic ownership could not be proven with the trusted loader rules."""


#: Backwards-compatible alias so callers outside this module keep working.
OwnershipUnverified = OwnershipUnverifiedError


#: Metadata marker key written by the existing demo loaders (see
#: ``load_asset_demo_data.DEMO_METADATA_KEY``): a part carrying this marker is
#: a managed demo-dataset record, which is the loaders' ownership proof.
DEMO_METADATA_KEY = 'asset_demo_data'

#: Tracked demo manifest used for identity-based ownership evidence.
DEMO_MANIFEST_PATH = Path(__file__).resolve().parents[1] / 'demo_machine_data.json'


def _normalized_identity(values: dict) -> tuple[str, str, str]:
    def norm(field_name: str) -> str:
        return str(values.get(field_name) or '').strip().casefold()

    return norm('manufacturer'), norm('model'), norm('serial')


def _manifest_identities() -> set[tuple[str, str, str]]:
    """Identity tuples owned by the tracked machine demo manifest."""
    try:
        data = json.loads(DEMO_MANIFEST_PATH.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise OwnershipUnverifiedError(f'Cannot read the demo manifest: {exc}') from exc
    identities = set()
    for record in data.get('machines', []):
        identities.add(_normalized_identity(record))
        identities.add(_normalized_identity(record.get('legacy_identity') or {}))
    return identities


def verify_machine_ownership(machine, evidence: dict) -> dict:
    """Prove synthetic ownership of one machine with the loaders' rules.

    The existing demo loaders accept a record as demo-owned only when a
    managed demo part (``asset_demo_data`` metadata marker) is linked, or when
    the manufacturer/model/serial identity matches a tracked demo manifest
    record (current or legacy identity). Names, generic ``demo`` tags and
    machine descriptions are never evidence, and neither is a mapping
    boolean — the returned dict records only what was verified in the
    database right now.
    """
    method = evidence.get('method')
    if method == 'managed_demo_part':
        wanted = {
            str(ipn).strip().casefold() for ipn in evidence.get('part_ipns') or []
        }
        for link in machine.machine_parts.select_related('part'):
            ipn = (link.part.IPN or '').strip().casefold()
            if ipn not in wanted:
                continue
            marker = link.part.get_metadata(DEMO_METADATA_KEY, {})
            if isinstance(marker, dict) and marker.get('kind') == 'part':
                return {
                    'method': method,
                    'verified_part_ipns': [link.part.IPN],
                    'marker_kind': marker.get('kind'),
                    'marker_schema_version': marker.get('schema_version'),
                }
        raise OwnershipUnverifiedError(
            f'Machine {machine.pk} links no managed demo part matching the '
            'declared ownership evidence'
        )
    if method == 'demo_manifest_identity':
        claimed = _normalized_identity(evidence.get('identity') or {})
        actual = _normalized_identity({
            'manufacturer': machine.manufacturer,
            'model': machine.model,
            'serial': machine.serial,
        })
        if actual != claimed or claimed not in _manifest_identities():
            raise OwnershipUnverifiedError(
                f'Machine {machine.pk} identity does not match the tracked '
                'demo manifest ownership evidence'
            )
        return {'method': method, 'verified_identity': list(claimed)}
    raise OwnershipUnverifiedError('Ownership evidence names no trusted loader rule')


def resolve_targets(
    fixture: contract.Fixture, mapping: contract.ResolvedMapping, actor
) -> tuple[dict, list]:
    """Resolve mapped identities against the live database, read-only.

    Returns ``(resolutions, conflicts)``. Conflicts never fail the plan: they
    are listed in the canonical plan and must be empty for apply to proceed.
    """
    from assets.demo_metrics_models import DemoMetricsMachine
    from assets.models import AssetLocation, AssetMachine

    resolutions: dict[str, TargetResolution] = {}
    conflicts: list[dict] = []
    machines = mapping.data['machines']

    actor_filter = None
    scope_denied = False
    try:
        from tasks.scope import machine_scope_filter

        actor_filter = machine_scope_filter(actor)
    except Exception:
        # Fail closed: an unresolvable actor scope is never treated as
        # "everything is in scope".
        actor_filter = None
        scope_unresolved = True
    else:
        scope_unresolved = False

    for alias, entry in sorted(machines.items()):
        machine = (
            AssetMachine.objects
            .select_related('client', 'physical_location')
            .filter(pk=entry['target_machine_id'])
            .first()
        )
        if machine is None:
            conflicts.append({
                'kind': 'machine',
                'key': alias,
                'code': 'MISSING_MACHINE',
            })
            if scope_unresolved:
                # Fail closed: an unresolvable actor scope denies even when
                # the resolution short circuits on an absent target.
                scope_denied = True
                conflicts.append({
                    'kind': 'machine',
                    'key': alias,
                    'code': 'SCOPE_UNRESOLVED',
                })
            continue
        # Scope boundary first: the actor's current scope is proven over this
        # machine before any state/conflict, telemetry, anomaly, claim or
        # ownership read. An early-exit conflict must never bypass it, or an
        # outsider could obtain an artifact (and live placement/client
        # disclosure) by mapping only drifted targets.
        in_scope = True
        if scope_unresolved:
            in_scope = False
            scope_denied = True
            conflicts.append({
                'kind': 'machine',
                'key': alias,
                'code': 'SCOPE_UNRESOLVED',
            })
        elif actor_filter is not None:
            in_scope = AssetMachine.objects.filter(actor_filter, pk=machine.pk).exists()
            if not in_scope:
                scope_denied = True
                conflicts.append({
                    'kind': 'machine',
                    'key': alias,
                    'code': 'OUT_OF_SCOPE',
                })
        if not in_scope:
            # A machine outside the actor's current scope gets no state,
            # ownership or verified-evidence reads. The recorded resolution
            # carries only the mapping's own input values — never live
            # placement/client data. The denial itself is the conflict, and
            # ``write_plan`` refuses to emit a plan carrying it — so the
            # denied machine's identity is never written out.
            resolutions[alias] = TargetResolution(
                alias=alias,
                target_id=entry['target_machine_id'],
                client_code=entry['client_code'],
                placement_id=None,
                placement_version=entry.get('expected_version') or 0,
                in_actor_scope=False,
            )
            continue
        if machine.client is None or machine.client.code != entry['client_code']:
            conflicts.append({
                'kind': 'machine',
                'key': alias,
                'code': 'CLIENT_MISMATCH',
            })
            continue
        if machine.placement_version != entry.get(
            'expected_version', machine.placement_version
        ):
            conflicts.append({
                'kind': 'machine',
                'key': alias,
                'code': 'PLACEMENT_CHANGED',
            })
            continue
        if machine.active is False:
            conflicts.append({
                'kind': 'machine',
                'key': alias,
                'code': 'MACHINE_INACTIVE',
            })
            continue
        existing_bindings = machine.signal_bindings.filter(active=True).exists()
        if existing_bindings:
            conflicts.append({
                'kind': 'machine',
                'key': alias,
                'code': 'REAL_TELEMETRY',
            })
        if machine.anomalies.filter(status__in=['open', 'acknowledged']).exists():
            conflicts.append({
                'kind': 'machine',
                'key': alias,
                'code': 'ACTIVE_ANOMALIES',
            })
        if DemoMetricsMachine.objects.filter(
            machine=machine, claim_active=True
        ).exists():
            conflicts.append({
                'kind': 'machine',
                'key': alias,
                'code': 'ALREADY_CLAIMED',
            })
        try:
            verified_evidence = verify_machine_ownership(
                machine, entry.get('ownership_evidence') or {}
            )
        except OwnershipUnverifiedError:
            verified_evidence = {}
            conflicts.append({
                'kind': 'machine',
                'key': alias,
                'code': 'OWNERSHIP_UNVERIFIED',
            })
        resolutions[alias] = TargetResolution(
            alias=alias,
            target_id=machine.pk,
            client_code=machine.client.code,
            placement_id=machine.physical_location_id,
            placement_version=machine.placement_version,
            in_actor_scope=in_scope,
            ownership_evidence=verified_evidence,
        )

    if scope_denied:
        # Fail closed before the location reads as well: an actor whose scope
        # does not cover the mapped targets receives no further target reads.
        return resolutions, conflicts

    for alias, entry in sorted(mapping.data['locations'].items()):
        location = AssetLocation.objects.filter(pk=entry['target_location_id']).first()
        if location is None:
            conflicts.append({
                'kind': 'location',
                'key': alias,
                'code': 'MISSING_LOCATION',
            })
            continue
        client_code = getattr(getattr(location, 'client', None), 'code', None)
        if client_code is not None and client_code != entry.get(
            'client_code', client_code
        ):
            conflicts.append({
                'kind': 'location',
                'key': alias,
                'code': 'CLIENT_MISMATCH',
            })

    return resolutions, conflicts


def build_plan(
    *,
    fixture: contract.Fixture,
    mapping: contract.ResolvedMapping,
    actor,
    session_anchor,
    resolutions: dict,
    conflicts: list,
) -> dict:
    """Assemble the canonical plan body (unsigned)."""
    session_anchor = reference.aware_utc(session_anchor)
    data = fixture.data
    machines = mapping.data['machines']
    bindings_by_machine: dict[str, list] = {}
    for binding in data['bindings']:
        bindings_by_machine.setdefault(binding['machine_key'], []).append(binding)

    create_effects: list[dict] = []
    for source in sorted(data['sources'], key=lambda s: s['key']):
        create_effects.append({
            'kind': 'source',
            'key': source['key'],
            'fingerprint': canonical_hash({
                'source': source,
                'session': mapping.session_key,
            }),
        })
    for binding in sorted(data['bindings'], key=lambda b: b['key']):
        create_effects.append({
            'kind': 'binding',
            'key': binding['key'],
            'machine_alias': binding['machine_key'],
            'external_key': contract.namespaced_tag(
                fixture.dataset_key,
                mapping.session_key,
                binding['machine_key'],
                binding['signal_key'],
            ),
            'fingerprint': canonical_hash({
                'binding': binding,
                'session': mapping.session_key,
            }),
        })
    for order in sorted(data['work_orders'], key=lambda o: o['key']):
        create_effects.append({
            'kind': 'work_order',
            'key': order['key'],
            'machine_alias': order['machine_key'],
            'requested_state': order['requested_state'],
            'fingerprint': canonical_hash({
                'work_order': order,
                'session': mapping.session_key,
            }),
        })
    for reading in sorted(data['readings'], key=lambda r: r['key']):
        create_effects.append({
            'kind': 'observation',
            'key': reading['key'],
            'machine_alias': reading['machine_key'],
            'fingerprint': canonical_hash({
                'reading': reading,
                'session': mapping.session_key,
            }),
        })

    reference_effects = [
        {
            'kind': 'machine',
            'key': alias,
            'target_id': resolution.target_id,
            'placement_id': resolution.placement_id,
            'placement_version': resolution.placement_version,
        }
        for alias, resolution in sorted(resolutions.items())
    ]
    reference_effects.extend(
        {'kind': 'location', 'key': alias, 'target_id': entry['target_location_id']}
        for alias, entry in sorted(mapping.data['locations'].items())
    )

    transition_sequence = {
        order['key']: TRANSITION_PATHS[order['requested_state']]
        for order in sorted(data['work_orders'], key=lambda o: o['key'])
    }

    historical = data['history']
    history_machines = sorted(
        {event['machine_key'] for event in historical['coverage_intervals']}
        | {event['machine_key'] for event in historical['downtime_intervals']}
    )

    body = {
        'plan_version': PLAN_VERSION,
        'dataset_key': fixture.dataset_key,
        'session_key': mapping.session_key,
        'target': dict(mapping.data['target']),
        'runtime_fingerprint': fingerprint.runtime_fingerprint(),
        'schema': {'app': 'assets', 'migration': _latest_migration('assets')},
        'fixture': {
            'schema_version': data['schema_version'],
            'canonical_sha256': fixture.canonical_sha256,
            'file_sha256': fixture.file_sha256,
        },
        'mapping_sha256': canonical_hash(mapping.data),
        'actor': {
            'id': getattr(actor, 'pk', None),
            'username': getattr(actor, 'get_username', lambda: '')(),
        },
        'scope': {
            'client_codes': sorted({
                resolution.client_code for resolution in resolutions.values()
            })
        },
        'effects': {
            'create': create_effects,
            'reference': reference_effects,
            'skip': [
                {'kind': 'oee_lab', 'reason': 'disabled separate calculation lab'},
                {'kind': 'anomaly', 'reason': 'condition-only initial release'},
            ],
            'conflict': sorted(
                conflicts, key=lambda item: (item['kind'], item['key'], item['code'])
            ),
        },
        'transition_sequence': transition_sequence,
        'cohort': {
            'current': {
                'aliases': sorted(machines),
                'configured_aliases': sorted(
                    m['key'] for m in data['machines'] if m['configured']
                ),
                'replay_aliases': sorted(
                    m['key']
                    for m in data['machines']
                    if m['scenario']
                    in ('fresh_normal', 'fresh_warning', 'fresh_critical')
                ),
                'bindings_per_machine': {
                    alias: len(bindings_by_machine.get(alias, []))
                    for alias in sorted(machines)
                },
            },
            'historical': {
                'aliases': history_machines,
                'coverage_intervals': len(historical['coverage_intervals']),
                'downtime_intervals': len(historical['downtime_intervals']),
                'start_at': historical['start_at'],
                'end_at': historical['end_at'],
                'attribution_mode': 'synthetic_scenario',
            },
        },
        'time_policy': {
            'fixture_as_of': data['as_of'],
            'anchor_at': reference.iso(session_anchor),
            'reporting_timezone': mapping.reporting_timezone,
            'stale_after_seconds': fixture.stale_after_seconds,
            'due_date_policy': 'application_date_rule_in_reporting_timezone',
        },
        'side_effect_policy': list(mapping.side_effect_policy),
        'expected_metrics': mapping.expected_counts,
        'expires_at': reference.iso(mapping.expires_at),
        'include_history': bool(mapping.history_import_approved),
    }
    return body


def _latest_migration(app_label: str) -> str:
    from django.db import connection
    from django.db.migrations.loader import MigrationLoader

    loader = MigrationLoader(connection)
    targets = loader.graph.leaf_nodes(app_label)
    return ','.join(f'{label}.{name}' for label, name in sorted(targets))


def assert_plan_matches_inputs(
    plan: dict,
    fixture: contract.Fixture,
    mapping: contract.ResolvedMapping,
    *,
    now=None,
    actor=None,
    require_unexpired: bool = True,
) -> None:
    """Verify a loaded plan against the loaded inputs before any effect.

    Identity/integrity checks always run. The time-bound reauthorization
    checks (:func:`assert_plan_authority_current`) run by default; a pure
    readback of an already-applied identical session may pass
    ``require_unexpired=False`` because it issues no new authorization - but
    any call that still owes effects must enforce the authority gate.
    """
    now = now or timezone.now()
    if actor is not None:
        planned_actor = plan.get('actor') or {}
        if planned_actor.get('username') != getattr(
            actor, 'get_username', lambda: ''
        )() or planned_actor.get('id') != getattr(actor, 'pk', None):
            raise PlanError(
                'ACTOR_MISMATCH',
                'The approved plan was created for a different actor than the '
                'runtime operator',
            )
    if plan['fixture']['canonical_sha256'] != fixture.canonical_sha256:
        raise PlanError('HASH_MISMATCH', 'Plan fixture hash does not match the fixture')
    if plan['mapping_sha256'] != canonical_hash(mapping.data):
        raise PlanError('HASH_MISMATCH', 'Plan mapping hash does not match the mapping')
    if plan.get('target') != dict(mapping.data['target']):
        raise PlanError(
            'TARGET_MISMATCH',
            'Plan target identity does not match the approved mapping',
        )
    if (
        plan['dataset_key'] != fixture.dataset_key
        or plan['session_key'] != mapping.session_key
    ):
        raise PlanError('PLAN_INVALID', 'Plan identity does not match the inputs')
    if require_unexpired:
        assert_plan_authority_current(plan, mapping, now=now)
    if plan['effects']['conflict']:
        raise PlanError('PLAN_CONFLICTS', 'Plan records unresolved conflicts')


def assert_plan_authority_current(
    plan: dict, mapping: contract.ResolvedMapping, *, now=None
) -> None:
    """Time-bound execution authority: plan and mapping expiry must be current.

    This gate authorizes *execution* (new effects) under the approved plan.
    Reading back an already-committed result issues no new authorization and
    does not pass through here; completing an owed (plan-approved) effect set
    later does, with the same strictness as the original apply.
    """
    now = now or timezone.now()
    expires = reference.timestamp(plan['expires_at'])
    if expires <= now:
        raise PlanError('PLAN_EXPIRED', 'Plan expiry has passed; replan')
    mapping_expiry = reference.timestamp(
        mapping.data['expires_at'].replace('Z', '+00:00')
    )
    if mapping_expiry <= now:
        raise PlanError('PLAN_EXPIRED', 'Mapping expiry has passed; replan')
