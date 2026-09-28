"""Ownership-aware cleanup for the EQUA demo metrics session (work package H).

Default mode is a read-only deletion/retention plan. Mutation requires the
separately approved cleanup hash of the exact plan body and an authorized
operator. Cleanup deletes only unchanged, session-created objects whose
deletion is explicitly allowed; borrowed machines/locations/users, referenced
records, operator-touched rows and protected evidence (receipts, the session
tombstone) are retained and reported.

Every ledger kind carries a **full** fingerprint of its record (work orders,
sources, bindings and the historical coverage/downtime intervals are hashed
from their live rows — a history row never reports its own stored seed
fingerprint as "current"). Deletions re-verify the fingerprint under row locks
immediately before the delete, and a work order is deleted only when its
dependent records (primary card, provenance events) are exactly the ones the
session recorded at seed time: any operator-dependent evidence keeps the whole
record. A name prefix or tag search is never a cleanup selector, and a rerun
is safe: already-retired records are tombstoned in the ledger, not recreated.
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone as dt_timezone

from django.db import transaction
from django.utils import timezone

from tasks.permissions import PLAN_WORKORDER, require_permission

from assets.demo_metrics_models import (
    DemoMetricsCoverageInterval,
    DemoMetricsDowntimeInterval,
    DemoMetricsObject,
    DemoMetricsReceipt,
    DemoMetricsSession,
)

from . import apply_service, effects, fingerprint, planner

CLEANUP_PLAN_VERSION = 'equa.demo-metrics-cleanup/2'

#: Kinds whose session-created rows may be deleted when unchanged.
DELETABLE_KINDS = (
    DemoMetricsObject.Kind.WORK_ORDER,
    DemoMetricsObject.Kind.SOURCE,
    DemoMetricsObject.Kind.BINDING,
    DemoMetricsObject.Kind.COVERAGE_INTERVAL,
    DemoMetricsObject.Kind.DOWNTIME_INTERVAL,
)

#: Dependency order for deletion (dependents before their targets).
KIND_ORDER = {
    DemoMetricsObject.Kind.WORK_ORDER: 0,
    DemoMetricsObject.Kind.BINDING: 1,
    DemoMetricsObject.Kind.SOURCE: 2,
    DemoMetricsObject.Kind.COVERAGE_INTERVAL: 3,
    DemoMetricsObject.Kind.DOWNTIME_INTERVAL: 4,
}


class CleanupError(Exception):
    """The cleanup plan or its approval is invalid."""

    def __init__(self, code: str, message: str):
        """Carry a stable machine-readable code plus a human message."""
        self.code = code
        self.message = message
        super().__init__(f'{code}: {message}')


UTC = dt_timezone.utc


def _instant_token(value: datetime) -> str:
    """Canonical instant token for the interval fingerprint schema.

    Aware values hash as their UTC instant (zero offset), and a naive value
    hashes identically to its zero-offset form: seed-time values and database
    read-backs of the same instant must never hash differently because one
    side carried a local offset or no offset at all.
    """
    value = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return value.isoformat()


def coverage_interval_fingerprint(interval) -> str:
    """Full governance fingerprint of one persisted coverage interval row.

    Shared schema for the seed written at history import and for the drift
    check: every governance-relevant field of the live row, hashed from the
    persisted values — never from the fixture event or a stored seed.
    """
    return planner.canonical_hash({
        'machine_id': interval.machine_id,
        'start_at': _instant_token(interval.start_at),
        'end_at': _instant_token(interval.end_at),
        'fully_observed': interval.fully_observed,
        'planned': interval.planned,
        'location_alias': interval.location_alias,
        'attribution_mode': interval.attribution_mode,
    })


def downtime_interval_fingerprint(interval) -> str:
    """Full governance fingerprint of one persisted downtime interval row."""
    return planner.canonical_hash({
        'machine_id': interval.machine_id,
        'start_at': _instant_token(interval.start_at),
        'end_at': _instant_token(interval.end_at),
        'loss_category': interval.loss_category,
        'failure_key': interval.failure_key,
        'coverage_id': interval.coverage_id,
        'location_alias': interval.location_alias,
        'attribution_mode': interval.attribution_mode,
    })


def object_fingerprint(row: DemoMetricsObject) -> str:
    """Current full fingerprint of the record a ledger row points at.

    Every mutable, governance-relevant field is covered for each kind, and the
    historical interval kinds are hashed from their live rows — a missing or
    changed interval is drift, never a silent match with the stored seed.
    """
    if row.kind == DemoMetricsObject.Kind.WORK_ORDER and row.work_order_id:
        order = row.work_order
        return planner.canonical_hash({
            'title': order.title,
            'description': order.description,
            'status': order.status,
            'lifecycle_status': order.lifecycle_status,
            'priority': order.priority,
            'due_date': order.due_date.isoformat() if order.due_date else None,
            'assignee': order.assignee,
            'assigned_to_id': order.assigned_to_id,
            'requested_by_id': order.requested_by_id,
            'tags': list(order.tags or []),
            'company': order.company,
            'company_contact_name': order.company_contact_name,
            'company_contact_phone': order.company_contact_phone,
            'job_number': order.job_number,
            'service_quote': order.service_quote,
            'is_active': order.is_active,
            'machine_id': order.machine_id,
            'customer_id': order.customer_id,
            'work_order_type': order.work_order_type,
            'hold_reason': order.hold_reason,
            'affected_component': order.affected_component,
            'affected_component_ref': order.affected_component_ref,
            'installed_part_id': order.installed_part_id,
            'lifecycle_version': order.lifecycle_version,
        })
    if row.kind == DemoMetricsObject.Kind.SOURCE and row.source_id:
        source = row.source
        return planner.canonical_hash({
            'name': source.name,
            'source_type': source.source_type,
            'connector_type': source.connector_type,
            'freshness_threshold_seconds': source.freshness_threshold_seconds,
            'site_key': source.site_key,
            'active': source.active,
            'config': source.config,
        })
    if row.kind == DemoMetricsObject.Kind.BINDING and row.binding_id:
        binding = row.binding
        return planner.canonical_hash({
            'external_key': binding.external_key,
            'display_name': binding.display_name,
            'signal_kind': binding.signal_kind,
            'unit': binding.unit,
            'warn_min': binding.warn_min,
            'warn_max': binding.warn_max,
            'critical_min': binding.critical_min,
            'critical_max': binding.critical_max,
            'transform': binding.transform,
            'active': binding.active,
            'machine_id': binding.machine_id,
            'source_id': binding.source_id,
        })
    if row.kind == DemoMetricsObject.Kind.COVERAGE_INTERVAL:
        interval = DemoMetricsCoverageInterval.objects.filter(
            session_id=row.session_id, event_key=row.fixture_key
        ).first()
        if interval is None:
            return 'missing:coverage_interval'
        return coverage_interval_fingerprint(interval)
    if row.kind == DemoMetricsObject.Kind.DOWNTIME_INTERVAL:
        interval = DemoMetricsDowntimeInterval.objects.filter(
            session_id=row.session_id, event_key=row.fixture_key
        ).first()
        if interval is None:
            return 'missing:downtime_interval'
        return downtime_interval_fingerprint(interval)
    return row.seed_fingerprint


def _dependent_drift(row: DemoMetricsObject) -> list:
    """Unrecorded or missing dependent records for a session work order.

    The ledger recorded the primary card and provenance events at seed time;
    anything beyond that set is operator-dependent evidence and must be
    preserved rather than cascaded away by a delete.
    """
    if row.kind != DemoMetricsObject.Kind.WORK_ORDER or not row.work_order_id:
        return []
    current = {
        (ref['kind'], ref['id'])
        for ref in apply_service._dependent_refs(row.work_order)
    }
    recorded = {(ref.get('kind'), ref.get('id')) for ref in (row.dependent_refs or [])}
    drift = []
    for kind, ref_id in sorted(current - recorded):
        drift.append({'kind': kind, 'id': ref_id, 'origin': 'unrecorded_dependent'})
    for kind, ref_id in sorted(recorded - current):
        drift.append({
            'kind': kind,
            'id': ref_id,
            'origin': 'missing_recorded_dependent',
        })
    return drift


def _inventory_row(row: DemoMetricsObject) -> dict:
    """Stable plan/report entry for one ledger row."""
    return {'kind': row.kind, 'key': row.fixture_key}


def build_cleanup_plan(session, actor) -> dict:
    """Read-only inventory: what may be deleted, what must be retained.

    Discloses only to an authorized actor with current scope for every
    session machine membership — the boundary is enforced here, not only in
    the command layer, so no caller reaches the inventory unauthenticated,
    role-less or out of scope.
    """
    _require_authorized_actor(actor)
    _require_actor_scope(actor, session)
    deletions = []
    retained_modified = []
    retained_referenced = []
    retained_dependent = []
    protected = []

    for row in session.ledger_objects.order_by('kind', 'fixture_key'):
        if row.origin != DemoMetricsObject.Origin.CREATED:
            retained_referenced.append({
                **_inventory_row(row),
                'reason': 'referenced_not_owned',
            })
            continue
        if row.kind not in DELETABLE_KINDS:
            protected.append({**_inventory_row(row), 'reason': 'protected_kind'})
            continue
        drift = _dependent_drift(row)
        if drift:
            retained_dependent.append({
                **_inventory_row(row),
                'reason': 'operator_dependent_evidence',
                'dependents': drift,
            })
            continue
        current = object_fingerprint(row)
        if row.seed_fingerprint and current != row.seed_fingerprint:
            retained_modified.append({
                **_inventory_row(row),
                'reason': 'operator_edit_preserved',
            })
            continue
        deletions.append({**_inventory_row(row), 'fingerprint': current})

    protected.append({
        'kind': 'session',
        'key': session.session_key,
        'reason': 'tombstone',
    })
    protected.append({
        'kind': 'receipts',
        'key': f'{session.receipts.count()} rows',
        'reason': 'audit_evidence',
    })

    body = {
        'cleanup_plan_version': CLEANUP_PLAN_VERSION,
        'session': {
            'id': str(session.pk),
            'dataset_key': session.dataset_key,
            'session_key': session.session_key,
            'plan_sha256': session.plan_sha256,
        },
        'deletions': sorted(deletions, key=lambda item: (item['kind'], item['key'])),
        'retained_modified': sorted(
            retained_modified, key=lambda item: (item['kind'], item['key'])
        ),
        'retained_referenced': sorted(
            retained_referenced, key=lambda item: (item['kind'], item['key'])
        ),
        'retained_dependent': sorted(
            retained_dependent, key=lambda item: (item['kind'], item['key'])
        ),
        'protected': sorted(protected, key=lambda item: (item['kind'], item['key'])),
    }
    body['plan_hash'] = planner.canonical_hash(body)
    return body


def apply_cleanup(session, actor, *, approved_cleanup_sha256: str, now=None) -> dict:
    """Apply an approved cleanup plan; only listed unchanged rows are deleted."""
    now = now or timezone.now()
    fingerprint.require_postgresql()
    _require_authorized_actor(actor)
    _require_actor_scope(actor, session)

    with transaction.atomic(), effects.synthetic_effects():
        apply_service.advisory_lock(
            f'{session.dataset_key}:{session.session_key}:cleanup'
        )
        locked = DemoMetricsSession.objects.select_for_update().get(pk=session.pk)

        # Recheck the boundary under the lock, before any effect: a scope
        # revoked since the entry check must fail closed here, and the repeat
        # readback below is disclosure too — never an authorization bypass.
        _require_authorized_actor(actor)
        _require_actor_scope(actor, locked)

        plan = build_cleanup_plan(locked, actor)
        if plan['plan_hash'] != approved_cleanup_sha256:
            raise CleanupError(
                'HASH_MISMATCH',
                'Approved cleanup hash does not match the current cleanup plan body',
            )
        if locked.status == DemoMetricsSession.Status.CLEANED:
            # Repeat cleanup is safe: the tombstone stays, nothing is recreated
            # and nothing further is deleted.
            return {
                'session_key': locked.session_key,
                'already_cleaned': True,
                'deleted': {},
                'retained_modified': [],
                'retained_referenced': plan['retained_referenced'],
                'retained_dependent': plan['retained_dependent'],
                'protected': plan['protected'],
            }
        if locked.status not in (
            DemoMetricsSession.Status.ACTIVE,
            DemoMetricsSession.Status.STOPPED,
            DemoMetricsSession.Status.EXPIRED,
        ):
            raise CleanupError(
                'SESSION_STATE', f'Session state {locked.status} cannot be cleaned'
            )

        deleted = {
            'work_order': 0,
            'source': 0,
            'binding': 0,
            'coverage_interval': 0,
            'downtime_interval': 0,
        }
        allowed = {(item['kind'], item['key']) for item in plan['deletions']}
        retained_modified = list(plan['retained_modified'])
        retained_dependent = list(plan['retained_dependent'])

        rows = sorted(
            locked.ledger_objects.all(),
            key=lambda row: (KIND_ORDER.get(row.kind, 9), row.fixture_key),
        )
        for row in rows:
            if (row.kind, row.fixture_key) not in allowed:
                continue
            kept = _delete_ledger_row(
                locked, row, retained_modified, retained_dependent
            )
            if kept:
                continue
            deleted[row.kind] += 1

        for membership in locked.machines.filter(claim_active=True):
            membership.claim_active = False
            membership.save(update_fields=['claim_active'])

        locked.status = DemoMetricsSession.Status.CLEANED
        locked.cleaned_at = apply_service._aware(now)
        locked.save(update_fields=['status', 'cleaned_at'])
        apply_service.claim_receipt(
            session=locked,
            operation_kind=DemoMetricsReceipt.Operation.CLEANUP,
            item_key='cleanup',
            digest=apply_service.request_hash(
                'cleanup', {'plan_hash': plan['plan_hash']}
            ),
        )

    return {
        'session_key': locked.session_key,
        'deleted': deleted,
        'retained_modified': retained_modified,
        'retained_referenced': plan['retained_referenced'],
        'retained_dependent': retained_dependent,
        'protected': plan['protected'],
    }


def _delete_ledger_row(session, row, retained_modified, retained_dependent) -> bool:
    """Delete one session-created record and its ledger row, or retain it.

    The target row is re-locked and its full fingerprint re-verified
    immediately before deletion (the plan is never trusted for later state),
    and dependent records must still be exactly the session-recorded set. The
    ledger row protects its target FK, so the row goes first inside a
    savepoint; a target whose deletion is blocked by other protected
    references is rolled back and retained/reported instead of force-deleted.
    """
    from django.db.models import ProtectedError

    row = DemoMetricsObject.objects.select_for_update().get(pk=row.pk)
    drift = _dependent_drift(row)
    if drift:
        retained_dependent.append({
            **_inventory_row(row),
            'reason': 'operator_dependent_evidence',
            'dependents': drift,
        })
        return True

    target = {
        DemoMetricsObject.Kind.WORK_ORDER: 'work_order',
        DemoMetricsObject.Kind.SOURCE: 'source',
        DemoMetricsObject.Kind.BINDING: 'binding',
    }.get(row.kind)
    if target and getattr(row, f'{target}_id'):
        target_model = {
            'work_order': _work_order_model,
            'source': _source_model,
            'binding': _binding_model,
        }[target]
        locked_target = (
            target_model()
            .objects.select_for_update()
            .get(pk=getattr(row, f'{target}_id'))
        )
        setattr(row, target, locked_target)
        if row.seed_fingerprint and object_fingerprint(row) != row.seed_fingerprint:
            retained_modified.append({
                **_inventory_row(row),
                'reason': 'operator_edit_preserved',
            })
            return True
        try:
            with transaction.atomic():
                row.delete()
                locked_target.delete()
        except ProtectedError:
            retained_modified.append({
                **_inventory_row(row),
                'reason': 'protected_references',
            })
            return True
        return False

    interval_model = {
        DemoMetricsObject.Kind.COVERAGE_INTERVAL: (
            DemoMetricsCoverageInterval,
            'coverage_interval',
        ),
        DemoMetricsObject.Kind.DOWNTIME_INTERVAL: (
            DemoMetricsDowntimeInterval,
            'downtime_interval',
        ),
    }.get(row.kind)
    if interval_model:
        model, _key = interval_model
        interval = (
            model.objects
            .select_for_update()
            .filter(session_id=session.pk, event_key=row.fixture_key)
            .first()
        )
        if interval is None or (
            row.seed_fingerprint and object_fingerprint(row) != row.seed_fingerprint
        ):
            retained_modified.append({
                **_inventory_row(row),
                'reason': 'operator_edit_preserved',
            })
            return True
        with transaction.atomic():
            row.delete()
            interval.delete()
        return False

    retained_modified.append({**_inventory_row(row), 'reason': 'unsupported_kind'})
    return True


def _work_order_model():
    from tasks.models import WorkOrder

    return WorkOrder


def _source_model():
    from assets.health_models import HealthSource

    return HealthSource


def _binding_model():
    from assets.health_models import MachineSignalBinding

    return MachineSignalBinding


def _require_authorized_actor(actor) -> None:
    """Require an authenticated, active operator holding the planning role.

    Shared authorization boundary for the cleanup planning and execution
    paths (and, through ``cli.authorize_operator``, every command entry):
    the same ``PLAN_WORKORDER`` standard ``verify_demo_metrics`` enforces.
    An anonymous or inactive actor and a missing permission fail identically
    and fail closed — authorization is never derived from a supplied name.
    """
    if (
        actor is None
        or not getattr(actor, 'is_authenticated', False)
        or not getattr(actor, 'is_active', False)
    ):
        raise CleanupError(
            'ACTOR_UNAUTHORIZED', 'Cleanup requires an authenticated, active actor'
        )
    try:
        require_permission(actor, PLAN_WORKORDER)
    except Exception as exc:
        raise CleanupError('ACTOR_UNAUTHORIZED', str(exc)) from exc


def _require_actor_scope(actor, session) -> None:
    """Require current machine scope for every session membership.

    Every retained membership is checked — claims already marked inactive
    included — before any disclosure or mutation: an inactive claim still
    names a machine whose state the plan discloses and whose rows the cleanup
    may delete. Scope resolution fails closed (``require_machine_scope``
    raises for an unresolved boundary or a machine without a client), so a
    role grant alone never reads as access to the session's machines.
    """
    from tasks.scope import require_machine_scope

    for membership in session.machines.all():
        try:
            require_machine_scope(actor, membership.machine)
        except Exception as exc:
            raise CleanupError('ACTOR_SCOPE', f'{membership.alias}: {exc}') from exc
