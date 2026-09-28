"""Governed apply for the EQUA demo metrics session (work package C).

One all-or-nothing database transaction:

1. verify plan/mapping/fixture hashes, expiry and actor authority before any
   effect;
2. runtime gate: PostgreSQL only (advisory locks, partial unique claims), with
   no test bypass;
3. deterministic transaction-level advisory lock plus the unique session claim;
4. machine claims under row locks with scope/ownership/configuration rechecks;
5. session-owned sources and bindings, governed work orders through wrappers
   around the existing scheduling/assignment/lifecycle services, and initial
   observations through the existing ingestion service;
6. durable receipts committed atomically with their effects — the unique
   ``(session, operation_kind, item_key)`` claim is what makes concurrent
   duplicate creates lose and exact replays return the stored receipt;
7. reconciliation of planned effects before the session is marked applied.

Nothing here fabricates safety acknowledgements, approvals, inventory or
closeout evidence, and no external effect (email, webhook, plugin, AI, control
call) is caused by the seed transaction.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import dataclass, field

from django.db import IntegrityError, connection, transaction
from django.utils import timezone

from tasks.permissions import PLAN_WORKORDER, require_permission
from tasks.scope import require_machine_scope

from assets.demo_metrics_models import (
    DemoMetricsCoverageInterval,
    DemoMetricsDowntimeInterval,
    DemoMetricsMachine,
    DemoMetricsObject,
    DemoMetricsReceipt,
    DemoMetricsSession,
)

from . import contract, effects, fingerprint, planner, reference

#: Durable execution identity: the ``(EXECUTION_OPERATION_KIND,
#: EXECUTION_ITEM_KEY)`` receipt binds the immutable apply payload - the
#: approved inputs plus the ``include_history`` execution choice - with its
#: canonical request hash. It rides the existing receipt identity (unique
#: ``(session, operation_kind, item_key)`` plus request hash) unchanged.
EXECUTION_OPERATION_KIND = DemoMetricsReceipt.Operation.APPLY_SESSION
EXECUTION_ITEM_KEY = 'apply'

#: Receipt kinds written by one apply execution. The apply summary and its
#: identical-retry readback both count exactly these - the durable execution
#: receipt plus one receipt per effect - and never later lifecycle receipts
#: (replay, stop, cleanup), so a retry returns the stored result unchanged.
APPLY_RECEIPT_KINDS = (
    DemoMetricsReceipt.Operation.APPLY_SESSION,
    DemoMetricsReceipt.Operation.APPLY_SOURCE,
    DemoMetricsReceipt.Operation.APPLY_BINDING,
    DemoMetricsReceipt.Operation.APPLY_WORK_ORDER,
    DemoMetricsReceipt.Operation.APPLY_CONTROL,
    DemoMetricsReceipt.Operation.APPLY_OBSERVATION,
)


class ApplyError(Exception):
    """The apply was refused or could not be completed; nothing was written."""

    def __init__(self, code: str, message: str):
        """Carry a stable machine-readable code plus a human message."""
        self.code = code
        super().__init__(f'{code}: {message}')


@dataclass
class ApplyResult:
    """Summary of one successful apply."""

    session_id: object
    session_key: str
    created: dict = field(default_factory=dict)
    receipts: int = 0
    #: True when this call resolved a lost-response retry against the stored
    #: result instead of applying new effects.
    reconciled: bool = False


def request_hash(operation: str, payload: dict) -> str:
    """Canonical request hash for a receipted operation."""
    body = json.dumps(
        {'operation': operation, 'payload': payload},
        sort_keys=True,
        separators=(',', ':'),
        default=str,
        allow_nan=False,
    )
    return hashlib.sha256(body.encode('utf-8')).hexdigest()


def _execution_digest(fixture, mapping, plan, include_history: bool) -> str:
    """Canonical request hash binding one immutable apply execution payload.

    The payload is the execution identity: the approved inputs and the
    ``include_history`` execution choice. Identical payload on the same
    operation key returns the stored receipt; any changed payload (including
    the execution flag in either direction) is a conflict.
    """
    return request_hash(
        'apply_session',
        {
            'dataset_key': fixture.dataset_key,
            'session_key': mapping.session_key,
            'plan_sha256': plan['plan_hash'],
            'mapping_sha256': planner.canonical_hash(mapping.data),
            'fixture_canonical_sha256': fixture.canonical_sha256,
            'fixture_file_sha256': fixture.file_sha256,
            'target_fingerprint': mapping.data['target'][
                'database_identity_fingerprint'
            ],
            'include_history': bool(include_history),
        },
    )


def claim_receipt(
    *,
    session,
    operation_kind: str,
    item_key: str,
    digest: str,
    effect_ids=None,
    outcome=DemoMetricsReceipt.Outcome.APPLIED,
    observed_at=None,
):
    """Claim the durable receipt identity for one operation on one item.

    Returns ``(receipt, created)``. An exact replay (same key, same canonical
    request) returns the stored receipt without new effects; the same key with
    a changed payload is a conflict.
    """
    existing = DemoMetricsReceipt.objects.filter(
        session=session, operation_kind=operation_kind, item_key=item_key
    ).first()
    if existing is not None:
        if existing.request_hash != digest:
            raise ApplyError(
                'RECEIPT_CONFLICT',
                f'{operation_kind}:{item_key} was already claimed with a different payload',
            )
        return existing, False
    try:
        with transaction.atomic():
            receipt = DemoMetricsReceipt.objects.create(
                session=session,
                operation_kind=operation_kind,
                item_key=item_key,
                request_hash=digest,
                outcome=outcome,
                effect_ids=list(effect_ids or []),
                observed_at=observed_at,
            )
    except IntegrityError:
        # A concurrent claim won the race; resolve it as a replay or conflict.
        existing = DemoMetricsReceipt.objects.get(
            session=session, operation_kind=operation_kind, item_key=item_key
        )
        if existing.request_hash != digest:
            raise ApplyError(
                'RECEIPT_CONFLICT',
                f'{operation_kind}:{item_key} was concurrently claimed with a different payload',
            )
        return existing, False
    return receipt, True


def advisory_lock(key: str) -> None:
    """Take a deterministic PostgreSQL transaction-level advisory lock."""
    stable = int.from_bytes(hashlib.sha256(key.encode('utf-8')).digest()[:8], 'big')
    stable &= 0x7FFFFFFFFFFFFFFF
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_xact_lock(%s)', [stable])


def _iso_digest(value: dict) -> str:
    return planner.canonical_hash(value)


def apply_session(
    *,
    fixture: contract.Fixture,
    mapping: contract.ResolvedMapping,
    plan: dict,
    actor,
    now=None,
    include_history: bool = False,
) -> ApplyResult:
    """Apply one approved plan atomically. See the module docstring."""
    now = reference.aware_utc(now or timezone.now())

    # 1. Input identity and authority, before any effect.
    if not getattr(actor, 'is_authenticated', False):
        raise ApplyError('ACTOR_UNAUTHORIZED', 'Apply requires an authenticated actor')
    require_permission(actor, PLAN_WORKORDER)
    # Identity/integrity always; the time-bound reauthorization (expiry) gate
    # is enforced below only where new effects will occur - a pure readback of
    # an identical already-committed session is not an execution.
    planner.assert_plan_matches_inputs(
        plan, fixture, mapping, now=now, actor=actor, require_unexpired=False
    )

    anchor = reference.timestamp(plan['time_policy']['anchor_at'])

    # 2. Runtime gate.
    fingerprint.require_postgresql()
    target_fingerprint = mapping.data['target']['database_identity_fingerprint']
    if target_fingerprint != fingerprint.fingerprint_id():
        raise ApplyError(
            'TARGET_MISMATCH',
            'Connected database fingerprint does not match the resolved mapping',
        )
    try:
        # The approved code/image identity is compared with the runtime
        # attestation here — never merely stored on the session row.
        fingerprint.require_runtime_identity_approved(
            str(mapping.data['target'].get('approved_commit_sha') or ''),
            str(mapping.data['target'].get('approved_image_digest') or ''),
        )
        fingerprint.assert_schema_unchanged(plan.get('runtime_fingerprint') or {})
    except fingerprint.RuntimeGateError as exc:
        raise ApplyError(exc.code, str(exc)) from exc

    if include_history and not plan.get('include_history'):
        raise ApplyError(
            'HISTORY_NOT_APPROVED',
            'This approved plan does not authorize the synthetic history import',
        )

    # Readiness preflight before any effect: requested states must be legal
    # lifecycle targets and every typed assignee must actually resolve.
    _preflight_readiness(fixture, mapping)

    with transaction.atomic(), effects.synthetic_effects():
        # 3. Dataset/target lock and unique session claim.
        advisory_lock(f'{fixture.dataset_key}:{mapping.session_key}')
        existing = DemoMetricsSession.objects.filter(
            dataset_key=fixture.dataset_key, session_key=mapping.session_key
        ).first()
        if existing is not None:
            # Lost-response retry of the identical approved apply: reconcile
            # against the stored result instead of a second effect set. The
            # immutable execution choice (include_history) is bound into the
            # durable execution receipt and re-checked there - a retry never
            # executes or completes any history phase.
            return _reconcile_existing_session(
                existing,
                fixture,
                mapping,
                plan,
                actor=actor,
                include_history=include_history,
            )
        # New execution requires current authority and a fresh anchor: these
        # gates authorize effects, so they are never skipped on this path.
        planner.assert_plan_authority_current(plan, mapping, now=now)
        _assert_anchor_fresh(plan, fixture, now)
        try:
            with transaction.atomic():
                session = DemoMetricsSession.objects.create(
                    dataset_key=fixture.dataset_key,
                    session_key=mapping.session_key,
                    mode=DemoMetricsSession.Mode.CURRENT,
                    status=DemoMetricsSession.Status.ACTIVE,
                    fixture_version=fixture.data['schema_version'],
                    fixture_canonical_sha256=fixture.canonical_sha256,
                    fixture_file_sha256=fixture.file_sha256,
                    mapping_sha256=planner.canonical_hash(mapping.data),
                    plan_sha256=plan['plan_hash'],
                    target_fingerprint=target_fingerprint,
                    code_identity=str(plan['target'].get('approved_commit_sha') or ''),
                    demo_owner=actor if getattr(actor, 'pk', None) else None,
                    demo_owner_identity=mapping.data['target'].get(
                        'demo_owner_identity'
                    )
                    or '',
                    anchor_at=_aware(anchor),
                    timezone_name=mapping.reporting_timezone,
                    expires_at=_aware(mapping.expires_at),
                    effect_policy=list(mapping.side_effect_policy),
                )
        except IntegrityError as exc:
            existing = DemoMetricsSession.objects.filter(
                dataset_key=fixture.dataset_key, session_key=mapping.session_key
            ).first()
            if existing is not None:
                # The concurrent winner committed the same approved apply.
                return _reconcile_existing_session(
                    existing,
                    fixture,
                    mapping,
                    plan,
                    actor=actor,
                    include_history=include_history,
                )
            raise ApplyError(
                'SESSION_EXISTS',
                'A session already exists for this dataset/session key',
            ) from exc

        # Durable execution identity: the immutable execution payload - the
        # approved inputs plus the ``include_history`` execution choice - is
        # claimed exactly once, in the same transaction as its effects. A
        # retry rebinds against this receipt: an identical payload returns it,
        # a changed payload conflicts and never executes.
        claim_receipt(
            session=session,
            operation_kind=EXECUTION_OPERATION_KIND,
            item_key=EXECUTION_ITEM_KEY,
            digest=_execution_digest(fixture, mapping, plan, include_history),
        )

        created = {
            'sources': 0,
            'bindings': 0,
            'work_orders': 0,
            'observations': 0,
            'controls': 0,
        }

        # 4. Machine claims under row locks.
        machine_rows = _claim_machines(fixture, mapping, session, actor)

        # 5a. Session-owned sources.
        sources = {}
        for source in sorted(fixture.data['sources'], key=lambda s: s['key']):
            row = _create_source(fixture, mapping, session, source)
            sources[source['key']] = row
            created['sources'] += 1

        # 5b. Session-owned bindings.
        bindings = {}
        machine_by_alias = dict(machine_rows)
        for binding in sorted(fixture.data['bindings'], key=lambda b: b['key']):
            row = _create_binding(
                fixture, mapping, session, binding, sources, machine_by_alias
            )
            bindings[binding['key']] = row
            created['bindings'] += 1

        # 5c. Governed work orders.
        for order in sorted(fixture.data['work_orders'], key=lambda o: o['key']):
            outcome = _create_work_order(
                fixture=fixture,
                mapping=mapping,
                session=session,
                order=order,
                machine=machine_by_alias[order['machine_key']],
                actor=actor,
                now=now,
            )
            if outcome == 'control':
                created['controls'] += 1
            else:
                created['work_orders'] += 1

        # 5d. Initial observations through the existing ingestion service.
        created['observations'] += _ingest_initial_observations(
            fixture=fixture,
            mapping=mapping,
            session=session,
            sources=sources,
            bindings=bindings,
            now=now,
        )

        # 5e. Optional historical import (phase 2 / package G).
        if include_history:
            from .history import import_history

            imported = import_history(fixture=fixture, mapping=mapping, session=session)
            created['coverage_intervals'] = imported['coverage']
            created['downtime_intervals'] = imported['downtime']

        # 6. Reconcile planned effects before marking applied.
        expected = mapping.expected_counts
        checks = {
            'sources': len(fixture.data['sources']),
            'bindings': expected['bindings'],
            'work_orders': expected['open_work_orders']
            + expected['completed_controls'],
            'observations': expected['observations'],
        }
        if include_history:
            # The executed history phase must match the reviewed cohort
            # exactly; a partial import rolls the whole apply back.
            history_expected = plan['cohort']['historical']
            checks['coverage_intervals'] = history_expected['coverage_intervals']
            checks['downtime_intervals'] = history_expected['downtime_intervals']
        for key, expected_total in checks.items():
            if key == 'work_orders':
                actual = created['work_orders'] + created['controls']
            else:
                actual = created[key]
            if actual != expected_total:
                raise ApplyError(
                    'RECONCILE_FAILED',
                    f'Planned {expected_total} {key} but applied {actual}',
                )

        # The apply summary counts exactly the durable receipts this
        # execution committed - the execution receipt plus one per effect -
        # the same set the identical-retry readback reports below.
        receipts = session.receipts.filter(
            operation_kind__in=APPLY_RECEIPT_KINDS
        ).count()

        session.plan_body = planner.plan_body(plan)
        session.applied_at = _aware(now)
        session.save(update_fields=['plan_body', 'applied_at'])

    return ApplyResult(
        session_id=session.pk,
        session_key=session.session_key,
        created=created,
        receipts=receipts,
    )


def _aware(value: dt.datetime) -> dt.datetime:
    """Follow the project's USE_TZ convention for stored datetimes."""
    from django.conf import settings

    if settings.USE_TZ:
        return timezone.make_aware(value) if timezone.is_naive(value) else value
    return timezone.make_naive(value) if timezone.is_aware(value) else value


def _preflight_readiness(fixture, mapping) -> None:
    """Readiness preflight before any effect.

    Every requested state must be a legal lifecycle target and every typed
    assignee the reviewed mapping resolves must exist and be active; anything
    else is refused up front rather than failing (and rolling back) mid-seed.
    """
    from django.contrib.auth import get_user_model

    for order in fixture.data['work_orders']:
        state = order['requested_state']
        if state not in planner.TRANSITION_PATHS:
            raise ApplyError(
                'READINESS_PREFLIGHT',
                f'{order["key"]} requests unknown lifecycle state {state!r}',
            )
    for key, assignee in sorted(mapping.assignees.items()):
        username = (assignee or {}).get('assigned_to_username')
        if not username:
            continue
        if (
            not get_user_model()
            .objects.filter(username=username, is_active=True)
            .exists()
        ):
            raise ApplyError('ASSIGNEE_UNRESOLVED', f'No active assignee for {key}')


def _reconcile_existing_session(
    existing, fixture, mapping, plan, *, actor, include_history: bool = False
) -> ApplyResult:
    """Resolve a lost-response retry against the stored session result.

    Identical approved inputs and an identical execution payload return the
    stored result (counts read back from the ledger/receipts) with
    ``reconciled=True``. This path is a pure readback of already-committed
    work under the dataset advisory lock: it performs no writes and never
    executes an effect. There is no *owed* history phase: the immutable
    ``include_history`` execution choice was bound into the durable execution
    receipt at apply time, so a changed payload - the execution flag flipping
    in either direction - is a conflict, never a second effect set.

    Readback stays possible after the freshness/expiry gates would refuse a
    new execution, but it keeps every identity, runtime and scope check: the
    *current* actor's machine scope is re-authorized over every retained
    membership before anything is disclosed (identity and role alone are
    never sufficient), and the committed history evidence must match the
    bound execution choice exactly. Missing or partial interval rows fail
    closed - they are never guessed from and never re-imported; only a
    cleaned session may lack them, and only with its own approved cleanup
    receipt as the durable deletion proof.
    """
    expected = {
        'plan_sha256': plan['plan_hash'],
        'mapping_sha256': planner.canonical_hash(mapping.data),
        'fixture_canonical_sha256': fixture.canonical_sha256,
        'fixture_file_sha256': fixture.file_sha256,
        'target_fingerprint': mapping.data['target']['database_identity_fingerprint'],
    }
    drifted = [
        key for key, value in expected.items() if getattr(existing, key) != value
    ]
    if drifted:
        raise ApplyError(
            'SESSION_INPUT_CONFLICT',
            'A session for this dataset/session key already exists with '
            f'different approved inputs ({", ".join(sorted(drifted))})',
        )
    # Immutable execution identity: the initial execution choice lives in the
    # durable execution receipt, never in guesses from interval rows. The
    # exact operation key with the same payload returns the stored receipt; a
    # changed payload conflicts.
    stored = existing.receipts.filter(
        operation_kind=EXECUTION_OPERATION_KIND, item_key=EXECUTION_ITEM_KEY
    ).first()
    if stored is None:
        raise ApplyError(
            'EXECUTION_EVIDENCE_MISSING',
            'The stored session carries no durable execution identity; '
            'the execution choice cannot be proven',
        )
    if stored.request_hash != _execution_digest(
        fixture, mapping, plan, include_history
    ):
        raise ApplyError(
            'RECEIPT_CONFLICT',
            f'{EXECUTION_OPERATION_KIND}:{EXECUTION_ITEM_KEY} was already claimed with '
            'a different payload (the include_history execution choice is immutable)',
        )
    executed_history = bool(include_history)

    # Re-authorize the CURRENT actor's scope over every retained membership
    # before any disclosure; identity and role alone are never sufficient.
    _reauthorize_memberships(existing, actor)

    # The committed history evidence must match the bound execution choice
    # exactly; nothing here writes, so a stopped/cleaned session is never
    # modified or recreated by a retry.
    history_counts = _verify_history_evidence(existing, plan, executed_history)

    ledger = existing.ledger_objects
    open_orders, controls = _work_control_split(
        ledger.filter(kind=DemoMetricsObject.Kind.WORK_ORDER)
    )
    created = {
        'sources': ledger.filter(kind=DemoMetricsObject.Kind.SOURCE).count(),
        'bindings': ledger.filter(kind=DemoMetricsObject.Kind.BINDING).count(),
        'work_orders': open_orders,
        'controls': controls,
        'observations': existing.receipts.filter(
            operation_kind=DemoMetricsReceipt.Operation.APPLY_OBSERVATION
        ).count(),
    }
    if executed_history:
        # The bound choice executed the history phase: report its real,
        # evidence-verified counts.
        created['coverage_intervals'] = history_counts['coverage']
        created['downtime_intervals'] = history_counts['downtime']
    return ApplyResult(
        session_id=existing.pk,
        session_key=existing.session_key,
        created=created,
        receipts=existing.receipts.filter(
            operation_kind__in=APPLY_RECEIPT_KINDS
        ).count(),
        reconciled=True,
    )


def _work_control_split(rows) -> tuple[int, int]:
    """Split ledger work-order rows into open work orders and synthetic controls.

    The split is proven from the durable evidence recorded when each row was
    created - the synthetic-control import provenance event named by the row's
    ``dependent_refs`` - never from the mutable live state of the work order.
    ``is_active`` and lifecycle status are operator state that ordinary
    governance legitimately moves; the stored result must not change when it
    does. A row whose recorded provenance cannot be read fails closed.
    """
    from tasks.models import WorkOrderEvent

    from assets.demo_history import IMPORTED_HISTORY_EVENT

    rows = list(rows)
    event_ids = {
        ref['id']
        for row in rows
        for ref in (row.dependent_refs or [])
        if ref.get('kind') == 'event'
    }
    control_events = set(
        WorkOrderEvent.objects.filter(
            pk__in=event_ids, event_type=IMPORTED_HISTORY_EVENT
        ).values_list('pk', flat=True)
    )
    open_orders = 0
    controls = 0
    for row in rows:
        events = [
            ref['id']
            for ref in (row.dependent_refs or [])
            if ref.get('kind') == 'event'
        ]
        if not events:
            raise ApplyError(
                'WORK_EVIDENCE_MISSING',
                f'Ledger row {row.fixture_key} records no provenance events; '
                'the work/control split cannot be proven',
            )
        if any(event_id in control_events for event_id in events):
            controls += 1
        else:
            open_orders += 1
    return open_orders, controls


def _reauthorize_memberships(session, actor) -> None:
    """Re-authorize the actor's current machine scope over retained memberships.

    Every retained membership - claim active or not - is re-checked against
    the actor's *current* maintenance scope before the stored result is
    disclosed. An actor whose scope was revoked after the apply learns
    nothing, even with the same identity and role.
    """
    from tasks.scope import ScopeError

    for membership in session.machines.select_related('machine').order_by('pk'):
        try:
            require_machine_scope(actor, membership.machine)
        except ScopeError as exc:
            raise ApplyError(
                'SCOPE_REVOKED',
                f'Actor scope no longer covers retained membership {membership.alias}',
            ) from exc


def _verify_history_evidence(session, plan, executed_history: bool) -> dict:
    """Verify committed history evidence against the bound execution choice.

    The durable execution receipt - never the presence of interval rows - is
    the completion proof. When the bound choice executed the history phase,
    the planned interval counts must be present exactly; when it did not, no
    history rows may exist at all. Missing, partial or unexpected evidence
    fails closed and is never guessed from. A cleaned session is the single
    exception: its rows were deleted by its own approved, receipted cleanup,
    so absence there is legitimate lifecycle state, and whatever is left
    (operator-retained rows) is reported as committed.
    """
    expected = plan['cohort']['historical']
    ledger = session.ledger_objects
    counts = {
        'coverage': ledger.filter(
            kind=DemoMetricsObject.Kind.COVERAGE_INTERVAL
        ).count(),
        'downtime': ledger.filter(
            kind=DemoMetricsObject.Kind.DOWNTIME_INTERVAL
        ).count(),
    }
    rows = {
        'coverage': DemoMetricsCoverageInterval.objects.filter(session=session).count(),
        'downtime': DemoMetricsDowntimeInterval.objects.filter(session=session).count(),
    }
    if session.status == DemoMetricsSession.Status.CLEANED:
        cleaned = session.receipts.filter(
            operation_kind=DemoMetricsReceipt.Operation.CLEANUP
        ).exists()
        if not cleaned:
            raise ApplyError(
                'HISTORY_EVIDENCE_MISSING',
                'A cleaned session without its cleanup receipt cannot prove '
                'the approved deletion of its history evidence',
            )
        return counts
    if executed_history:
        for key, plan_key in (
            ('coverage', 'coverage_intervals'),
            ('downtime', 'downtime_intervals'),
        ):
            wanted = expected[plan_key]
            if counts[key] != wanted or rows[key] != wanted:
                raise ApplyError(
                    'HISTORY_EVIDENCE_INCOMPLETE',
                    f'History evidence for {plan_key} is missing or partial '
                    f'(expected {wanted}, found {counts[key]} ledger/{rows[key]} rows); '
                    'a retry never re-imports the history phase',
                )
    elif any(counts.values()) or any(rows.values()):
        raise ApplyError(
            'HISTORY_EVIDENCE_UNEXPECTED',
            'History rows exist although the immutable execution choice '
            'never imported them',
        )
    return counts


def _assert_anchor_fresh(plan, fixture, now) -> None:
    """Refuse effects under a stale planning anchor (freshness budget)."""
    anchor = reference.timestamp(plan['time_policy']['anchor_at'])
    staleness = (now - anchor).total_seconds()
    if staleness > fixture.stale_after_seconds:
        raise ApplyError(
            'STALE_ANCHOR',
            'Session anchor is older than the reviewed freshness budget; replan',
        )


def _claim_machines(fixture, mapping, session, actor):
    """Claim every mapped machine under row locks; fail closed on drift."""
    from assets.models import AssetMachine

    ids = sorted(
        entry['target_machine_id'] for entry in mapping.data['machines'].values()
    )
    locked = list(
        AssetMachine.objects
        .select_for_update(of=('self',))
        .select_related('client')
        .filter(pk__in=ids)
        .order_by('pk')
    )
    by_id = {machine.pk: machine for machine in locked}
    rows = {}
    for alias, entry in sorted(mapping.data['machines'].items()):
        machine = by_id.get(entry['target_machine_id'])
        if machine is None:
            raise ApplyError(
                'MACHINE_MISSING', f'Mapped machine for {alias} disappeared'
            )
        if machine.client is None or machine.client.code != entry['client_code']:
            raise ApplyError(
                'CLIENT_MISMATCH', f'Mapped machine for {alias} changed client'
            )
        if machine.placement_version != entry.get(
            'expected_version', machine.placement_version
        ):
            raise ApplyError('PLACEMENT_CHANGED', f'Mapped machine for {alias} moved')
        require_machine_scope(actor, machine)
        try:
            # Trusted loader ownership rules, re-verified under the row lock.
            # The mapping boolean is never turned into evidence.
            verified_evidence = planner.verify_machine_ownership(
                machine, entry.get('ownership_evidence') or {}
            )
        except planner.OwnershipUnverifiedError as exc:
            raise ApplyError('OWNERSHIP_UNVERIFIED', f'{alias}: {exc}') from exc
        if machine.signal_bindings.filter(active=True).exists():
            raise ApplyError(
                'REAL_TELEMETRY',
                f'Machine {alias} carries non-session telemetry bindings',
            )
        if machine.anomalies.filter(status__in=['open', 'acknowledged']).exists():
            raise ApplyError(
                'ACTIVE_ANOMALIES', f'Machine {alias} carries active anomalies'
            )
        _claim_one_machine(session, alias, entry, machine, verified_evidence)
        rows[alias] = machine
    return rows


def _claim_one_machine(session, alias, entry, machine, verified_evidence):
    """Create one machine claim; a concurrent claim loses the race cleanly."""
    try:
        with transaction.atomic():
            return DemoMetricsMachine.objects.create(
                session=session,
                machine=machine,
                alias=alias,
                client_code=entry['client_code'],
                placement_id=machine.physical_location_id,
                placement_version=machine.placement_version,
                # Only database-verified provenance is recorded, exactly as
                # verified above — evidence is discovered, never invented.
                ownership_evidence=dict(verified_evidence),
                expected_fingerprint=_iso_digest({
                    'machine': machine.pk,
                    'version': machine.placement_version,
                }),
                claim_active=True,
            )
    except IntegrityError as exc:
        raise ApplyError(
            'MACHINE_CLAIMED',
            f'Machine for {alias} is claimed by another active session',
        ) from exc


def _seed_ledger_row(row):
    """Set the ledger row's identity in the cleanup fingerprint space."""
    from . import cleanup

    if row.work_order_id:
        # The governed services transition through their own locked instance;
        # ours is stale until refreshed and must not seed the fingerprint.
        row.work_order.refresh_from_db()
    fingerprint = cleanup.object_fingerprint(row)
    row.seed_fingerprint = fingerprint
    row.last_fingerprint = fingerprint
    row.save(update_fields=['seed_fingerprint', 'last_fingerprint'])
    return row


def _create_source(fixture, mapping, session, source):
    """Create one session-owned synthetic health source."""
    from assets.demo_metrics_models import DemoMetricsObject, DemoMetricsReceipt
    from assets.health_models import HealthSource, SourceType

    scope = (mapping.source_scope or {}).get(source['key']) or {}
    client_code = scope.get('client_code')
    security_site_key = scope.get('site_key')
    if not client_code or not security_site_key:
        raise ApplyError(
            'SOURCE_SCOPE_UNMAPPED',
            f'{source["key"]} has no approved security scope in the mapping',
        )
    name = f'{fixture.dataset_key}/{session.session_key}/{source["key"]}'
    digest = _iso_digest({'source': source, 'session': session.session_key})
    receipt, created = claim_receipt(
        session=session,
        operation_kind=DemoMetricsReceipt.Operation.APPLY_SOURCE,
        item_key=source['key'],
        digest=digest,
    )
    if not created:
        return HealthSource.objects.get(name=name)
    row = HealthSource.objects.create(
        name=name,
        source_type=SourceType.MANUAL,
        connector_type='equa-demo-metrics',
        freshness_threshold_seconds=fixture.stale_after_seconds,
        # The approved security scope comes from the mapping. The fixture's
        # physical location aliases (site_key) are never substituted for it.
        site_key=security_site_key,
        config={
            'synthetic': True,
            'session_key': session.session_key,
            'client_code': client_code,
        },
    )
    _seed_ledger_row(
        DemoMetricsObject.objects.create(
            session=session,
            kind=DemoMetricsObject.Kind.SOURCE,
            fixture_key=source['key'],
            origin=DemoMetricsObject.Origin.CREATED,
            source=row,
        )
    )
    receipt.effect_ids = [row.pk]
    receipt.save(update_fields=['effect_ids'])
    return row


def _create_binding(fixture, mapping, session, binding, sources, machine_by_alias):
    """Create one session-owned synthetic signal binding with a namespaced tag."""
    from assets.demo_metrics_models import DemoMetricsObject, DemoMetricsReceipt
    from assets.health_models import MachineSignalBinding

    signal = next(
        s
        for s in fixture.data['signal_definitions']
        if s['key'] == binding['signal_key']
    )
    tag = contract.namespaced_tag(
        fixture.dataset_key,
        session.session_key,
        binding['machine_key'],
        binding['signal_key'],
    )
    digest = _iso_digest({'binding': binding, 'session': session.session_key})
    claim_receipt(
        session=session,
        operation_kind=DemoMetricsReceipt.Operation.APPLY_BINDING,
        item_key=binding['key'],
        digest=digest,
    )
    machine = machine_by_alias[binding['machine_key']]
    source = sources[binding['source_key']]
    if MachineSignalBinding.objects.filter(source=source, external_key=tag).exists():
        raise ApplyError(
            'DUPLICATE_TAG', f'External tag {tag} already bound for this source'
        )
    row = MachineSignalBinding.objects.create(
        machine=machine,
        source=source,
        external_key=tag,
        display_name=f'{binding["machine_key"]} {binding["signal_key"]}',
        signal_kind=binding['signal_key'],
        unit=signal['unit'],
        warn_max=signal['warning_at'],
        critical_max=signal['critical_at'],
        transform={},
        active=True,
    )
    _seed_ledger_row(
        DemoMetricsObject.objects.create(
            session=session,
            kind=DemoMetricsObject.Kind.BINDING,
            fixture_key=binding['key'],
            origin=DemoMetricsObject.Origin.CREATED,
            binding=row,
        )
    )
    return row


def _create_work_order(*, fixture, mapping, session, order, machine, actor, now):
    """Create one governed work order (or synthetic completed control).

    Wraps the existing scheduling/assignment/lifecycle services: explicit actor
    scope and permission first, legal lifecycle edges only, readiness evaluated
    at every transition — nothing fabricated to satisfy the fixture.
    """
    from tasks.models import WorkOrder, WorkOrderType
    from tasks.services import scheduling, work_orders

    key = order['key']
    digest = _iso_digest({'work_order': order, 'session': session.session_key})
    receipt, claimed = claim_receipt(
        session=session,
        operation_kind=DemoMetricsReceipt.Operation.APPLY_WORK_ORDER,
        item_key=key,
        digest=digest,
    )
    if not claimed:
        return 'control' if order['requested_state'] == 'completed' else 'open'

    require_permission(actor, PLAN_WORKORDER)
    require_machine_scope(actor, machine)

    priority = contract.translate_priority(order['suggested_priority'])
    work_type = {
        'preventive': WorkOrderType.PREVENTIVE,
        'corrective': WorkOrderType.CORRECTIVE,
        'inspection': WorkOrderType.INSPECTION,
    }.get(order['scenario_type'], WorkOrderType.OTHER)
    fixture_due = reference.timestamp(order['due_at'])
    anchor = _anchor_session(session, now)
    due_date, original_due_instant = contract.translate_due_date(
        fixture_due, fixture.as_of, anchor, mapping.reporting_timezone
    )

    result = scheduling.create_work_order(
        actor=actor,
        idempotency_key=f'{session.session_key}:{key}:create',
        title=order['title'][:200],
        machine_id=machine.pk,
        priority=priority,
        work_order_type=work_type,
        due_date=due_date,
        description=(
            f'Synthetic demo scenario {order["scenario_type"]}; '
            f'due instant {original_due_instant}.'
        ),
    )
    work_order = WorkOrder.objects.get(pk=result.work_order_id)
    version = work_order.lifecycle_version

    if order['requested_state'] == 'completed':
        _import_synthetic_control(
            work_order=work_order,
            mapping=mapping,
            session=session,
            order=order,
            due_date=due_date,
        )
        receipt.effect_ids = [work_order.pk]
        receipt.save(update_fields=['effect_ids'])
        _seed_ledger_row(
            DemoMetricsObject.objects.create(
                session=session,
                kind=DemoMetricsObject.Kind.WORK_ORDER,
                fixture_key=key,
                origin=DemoMetricsObject.Origin.CREATED,
                work_order=work_order,
                target_version=work_order.lifecycle_version,
                dependent_refs=_dependent_refs(work_order),
            )
        )
        return 'control'

    # Typed assignee where the reviewed mapping resolves one.
    assignee = mapping.assignees.get(key)
    if assignee and assignee.get('assigned_to_username'):
        from django.contrib.auth import get_user_model

        user = (
            get_user_model()
            .objects.filter(username=assignee['assigned_to_username'], is_active=True)
            .first()
        )
        if user is None:
            raise ApplyError('ASSIGNEE_UNRESOLVED', f'No active assignee for {key}')
        result = work_orders.assign_work_order(
            work_order_id=work_order.pk,
            assigned_to=user,
            actor=actor,
            expected_version=version,
            idempotency_key=f'{session.session_key}:{key}:assign',
        )
        version = result.lifecycle_version

    for state in planner.TRANSITION_PATHS[order['requested_state']]:
        result = work_orders.transition_work_order(
            work_order_id=work_order.pk,
            to_status=state,
            actor=actor,
            expected_version=version,
            idempotency_key=f'{session.session_key}:{key}:transition:{state}',
            reason='Synthetic demo session setup',
        )
        version = result.lifecycle_version

    receipt.effect_ids = [work_order.pk]
    receipt.save(update_fields=['effect_ids'])
    _seed_ledger_row(
        DemoMetricsObject.objects.create(
            session=session,
            kind=DemoMetricsObject.Kind.WORK_ORDER,
            fixture_key=key,
            origin=DemoMetricsObject.Origin.CREATED,
            work_order=work_order,
            target_version=work_order.lifecycle_version,
            dependent_refs=_dependent_refs(work_order),
        )
    )
    return 'open'


def _dependent_refs(work_order):
    """Record dependent records (primary card, provenance events) explicitly."""
    from tasks.models import KanbanCard, WorkOrderEvent

    refs = []
    card = (
        KanbanCard.objects
        .filter(work_order=work_order, card_kind=KanbanCard.KIND_WORK_ORDER)
        .order_by('pk')
        .first()
    )
    if card is not None:
        refs.append({'kind': 'card', 'id': card.pk})
    refs.extend(
        {'kind': 'event', 'id': event_pk}
        for event_pk in WorkOrderEvent.objects
        .filter(work_order=work_order)
        .order_by('pk')
        .values_list('pk', flat=True)
    )
    return refs


def _import_synthetic_control(*, work_order, mapping, session, order, due_date):
    """Import one completed/inactive control through the owned synthetic wrapper.

    Uses ``assets.demo_history.normalize_completed_history_card`` for the
    explicit synthetic import, then synchronizes the primary Kanban card's
    stage/state because the normalizer does not do that itself.
    """
    from tasks.models import KanbanColumn, WorkOrder, WorkOrderEvent

    from assets.demo_history import (
        IMPORTED_HISTORY_EVENT,
        normalize_completed_history_card,
    )

    dataset = f'{session.dataset_key}/{session.session_key}'
    normalize_completed_history_card(
        work_order,
        record_date=due_date,
        dataset=dataset,
        timezone_name=mapping.reporting_timezone,
        extra_metadata={
            'synthetic': True,
            'session_key': session.session_key,
            'fixture_key': order['key'],
            'attribution_mode': 'synthetic_scenario',
        },
    )
    terminal = KanbanColumn.terminal_key() or WorkOrder.STATUS_DONE
    card = work_order.primary_card
    if card is not None and (card.status != terminal or card.is_active):
        card.status = terminal
        card.is_active = False
        card.save(update_fields=['status', 'is_active', 'updated_at'])
    # The provenance event must exist exactly once and carry synthetic metadata.
    event = WorkOrderEvent.objects.filter(
        work_order=work_order, event_type=IMPORTED_HISTORY_EVENT
    ).first()
    if event is None or not event.metadata.get('synthetic'):
        raise ApplyError(
            'CONTROL_PROVENANCE', 'Synthetic control lacks its provenance event'
        )


def _ingest_initial_observations(*, fixture, mapping, session, sources, bindings, now):
    """Ingest the translated initial observations; fail on any partial batch."""
    from assets.demo_metrics_models import DemoMetricsReceipt
    from machine_health.services.ingestion import ingest_readings

    readings = contract.translate_readings(
        fixture, mapping, _anchor_session(session, now)
    )
    by_source: dict[str, list] = {}
    for reading in readings:
        by_source.setdefault(reading.source_alias, []).append(reading)

    accepted_total = 0
    for source_alias, batch in sorted(by_source.items()):
        source = sources[source_alias]
        payloads = [
            {
                'external_key': reading.external_key,
                'value': reading.value,
                'observed_at': reading.observed_at,
                'quality': reading.quality,
                'sequence': reading.sequence,
            }
            for reading in batch
        ]
        result = ingest_readings(source, payloads, now=_aware(now))
        if (
            result.accepted != len(payloads)
            or result.unmapped
            or result.rejected
            or result.replayed
        ):
            raise ApplyError(
                'PARTIAL_BATCH',
                f'Observation batch for {source_alias} was not fully accepted: '
                f'{result.as_dict()}',
            )
        for reading in batch:
            claim_receipt(
                session=session,
                operation_kind=DemoMetricsReceipt.Operation.APPLY_OBSERVATION,
                item_key=reading.item_key,
                digest=_iso_digest({
                    'external_key': reading.external_key,
                    'value': reading.value,
                    'observed_at': reference.iso(reading.observed_at),
                }),
                observed_at=_aware(reading.observed_at),
            )
            accepted_total += 1
    return accepted_total


def _anchor_session(session, now):
    """Return the session anchor as an aware UTC datetime."""
    anchor = session.anchor_at
    if timezone.is_naive(anchor):
        return anchor.replace(tzinfo=dt.timezone.utc)
    return anchor
