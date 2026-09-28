"""Bounded replay feed and stop protocol (work package F).

One manual execution drives a bounded live feed: session authority, allowed
aliases and configuration are re-checked on every batch under the session and
machine row locks, each batch runs in its own short transaction, emits genuine
new timestamps and commits with its own durable receipt. Exact replay of an
operation key with an identical canonical payload — batch identity, value,
**timestamp and quality included** — returns the stored receipt with no new
effects; the same key with any changed payload is a conflict.

Every entry point authorizes the actor: an authenticated trusted operator with
the work-order planning permission, and per-machine maintenance scope for each
machine a batch touches. The durable feed claim binds the starting operator,
and a repeated ``run_replay`` never establishes a second active feed: when a
feed claim already exists the loop refuses to emit (recover lost responses
with explicit ``replay_batch`` receipt replays instead).

Stopping serializes with ingestion on the session row: no batch can commit
after the stop boundary.
"""

from __future__ import annotations

import time as time_module

from django.db import transaction
from django.utils import timezone

from tasks.permissions import PLAN_WORKORDER, require_permission
from tasks.scope import require_machine_scope

from assets.demo_metrics_models import (
    DemoMetricsObject,
    DemoMetricsReceipt,
    DemoMetricsSession,
)

from . import apply_service, contract, effects, fingerprint, planner, reference

#: Receipt item key of the durable feed claim.
FEED_CLAIM_KEY = 'feed/claim'

#: Machine aliases allowed to receive replayed samples (fresh-scenario machines).
DEFAULT_REPLAY_ALIASES = ('A01', 'A02', 'B01')

#: Batch identity must be a simple stable slug (receipt item key component).
MAX_BATCH_KEY_LENGTH = 64

#: Hard bounds for one manual feed execution.
MAX_INTERVAL_SECONDS = 3600
MAX_DURATION_SECONDS = 3600

#: The quality class every replayed sample is emitted with.
REPLAY_QUALITY = 'good'


class ReplayError(Exception):
    """The replay feed refused to start or to commit a batch."""

    def __init__(self, code: str, message: str):
        """Carry a stable machine-readable code plus a human message."""
        self.code = code
        super().__init__(f'{code}: {message}')


def _aware(value):
    return apply_service._aware(value)


def _require_actor(actor) -> None:
    """Authorize the operator before any replay protocol action."""
    if not getattr(actor, 'is_authenticated', False):
        raise ReplayError(
            'ACTOR_UNAUTHORIZED', 'Replay requires an authenticated actor'
        )
    try:
        require_permission(actor, PLAN_WORKORDER)
    except Exception as exc:
        raise ReplayError('ACTOR_UNAUTHORIZED', str(exc)) from exc


def _session_scope_error(actor, session):
    """Shared current-scope check for every session membership.

    Claims already marked inactive are included: an inactive claim still names
    a machine the session holds and its state discloses. Scope resolution fails
    closed (unresolved boundary or machine without a client), so a role grant
    alone never authorizes a session-level effect or readback. Returns the
    denial to raise, or ``None`` when the actor's current scope covers the
    whole session. The cleanup service owns the boundary; this is the same
    check, raised in the replay vocabulary.
    """
    from . import cleanup

    try:
        cleanup._require_actor_scope(actor, session)
    except cleanup.CleanupError as exc:
        return ReplayError(exc.code, exc.message)
    return None


def _feed_digest(session, actor) -> str:
    """Canonical identity of one feed-start claim by this operator."""
    return apply_service.request_hash(
        'replay_feed_start', {'session': str(session.pk), **_actor_identity(actor)}
    )


def _locked_session(session):
    """Reload and lock the session row (stop/ingest serialization point)."""
    return DemoMetricsSession.objects.select_for_update().get(pk=session.pk)


def replay_aliases(session) -> tuple[str, ...]:
    """Machine aliases the reviewed plan allows to receive replayed samples."""
    body = session.plan_body or {}
    aliases = body.get('cohort', {}).get('current', {}).get('replay_aliases')
    if aliases:
        return tuple(aliases)
    return DEFAULT_REPLAY_ALIASES


def _actor_identity(actor) -> dict:
    return {
        'actor_id': getattr(actor, 'pk', None),
        'actor': getattr(actor, 'get_username', lambda: '')(),
    }


def start_feed(session, actor, *, now=None):
    """Claim the durable feed identity for this session.

    The claim is bound to the starting operator. A repeated start by the same
    operator returns ``already_started`` instead of establishing a second
    active feed; a claim attempt by another *authorized* operator is a
    ``FEED_CONFLICT``. A scope-denied actor uniformly receives the scope
    denial — never the claim-conflict oracle — and the receipt claim seam is
    never even queried before the boundary. The claim is refused for
    stopped/expired sessions.
    """
    now = now or timezone.now()
    _require_actor(actor)
    fingerprint.require_postgresql()
    with transaction.atomic(), effects.synthetic_effects():
        locked = _locked_session(session)
        # Current scope for every membership first: no effect, no repeat
        # readback — not even a receipt query — happens before this boundary.
        scope_error = _session_scope_error(actor, locked)
        if scope_error is not None:
            raise scope_error
        _require_feedable(locked, now)
        digest = _feed_digest(locked, actor)
        try:
            _receipt, created = apply_service.claim_receipt(
                session=locked,
                operation_kind=DemoMetricsReceipt.Operation.REPLAY_OBSERVATION,
                item_key=FEED_CLAIM_KEY,
                digest=digest,
            )
        except apply_service.ApplyError as exc:
            if exc.code == 'RECEIPT_CONFLICT':
                raise ReplayError(
                    'FEED_CONFLICT', 'Feed claim exists for another operator or payload'
                ) from exc
            raise
        return {
            'started': created,
            'already_started': not created,
            'session_key': locked.session_key,
        }


def _require_feedable(session, now):
    """Session authority/expiry gate shared by start and every batch."""
    if session.status != DemoMetricsSession.Status.ACTIVE:
        raise ReplayError(
            'SESSION_STOPPED', f'Session is {session.status}; replay is revoked'
        )
    expires = session.expires_at
    if timezone.is_naive(expires):
        compare_now = _aware(now) if timezone.is_aware(now) else now
    else:
        compare_now = now if timezone.is_aware(now) else timezone.make_aware(now)
    if compare_now > expires:
        session.status = DemoMetricsSession.Status.EXPIRED
        session.save(update_fields=['status'])
        raise ReplayError('SESSION_EXPIRED', 'Session expiry has passed')


def _validate_batch_inputs(batch_key, values, candidates):
    """Bounds and vocabulary checks for one batch before any effect."""
    if (
        not isinstance(batch_key, str)
        or not batch_key
        or len(batch_key) > MAX_BATCH_KEY_LENGTH
    ):
        raise ReplayError('BAD_BATCH', 'batch_key must be a non-empty bounded slug')
    if not all(ch.isalnum() or ch in '-_.' for ch in batch_key):
        raise ReplayError('BAD_BATCH', 'batch_key must be a simple stable slug')
    if not isinstance(values, dict):
        raise ReplayError('BAD_BATCH', 'values must map (alias, signal) to a number')
    unknown = sorted(set(values) - set(candidates))
    if unknown:
        raise ReplayError(
            'BAD_BATCH', f'values override signals outside the replay cohort: {unknown}'
        )
    for pair, value in values.items():
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or value != value
            or value in (float('inf'), float('-inf'))
        ):
            raise ReplayError('BAD_BATCH', f'value for {pair} must be a finite number')


def _reevaluate_ownership(membership) -> None:
    """Re-prove the claim's synthetic ownership with the loaders' rules."""
    from assets.models import AssetMachine

    machine = AssetMachine.objects.get(pk=membership.machine_id)
    stored = membership.ownership_evidence or {}
    method = stored.get('method')
    if method == 'managed_demo_part':
        evidence = {
            'method': method,
            'part_ipns': stored.get('verified_part_ipns') or [],
        }
    elif method == 'demo_manifest_identity':
        # strict=False: verified evidence carries exactly three values but a
        # missing/partial stored list is anticipated; a short identity is then
        # rejected by the ownership verification below (OWNERSHIP_DRIFT).
        identity = dict(
            zip(
                ('manufacturer', 'model', 'serial'),
                stored.get('verified_identity') or [],
                strict=False,
            )
        )
        evidence = {'method': method, 'identity': identity}
    else:
        raise ReplayError(
            'OWNERSHIP_DRIFT', 'Machine claim carries no verified ownership'
        )
    try:
        planner.verify_machine_ownership(machine, evidence)
    except planner.OwnershipUnverified as exc:
        raise ReplayError('OWNERSHIP_DRIFT', str(exc)) from exc


def _recheck_scope(membership, actor) -> None:
    """Re-check one machine's current scope before its receipt claims.

    The claim loop's identical-receipt readback is disclosure too, so the
    scope boundary is re-proven immediately before each machine's claims —
    not only once at the top of the batch.
    """
    from assets.models import AssetMachine

    machine = AssetMachine.objects.get(pk=membership.machine_id)
    try:
        require_machine_scope(actor, machine)
    except Exception as exc:
        raise ReplayError('ACTOR_SCOPE', str(exc)) from exc


def _check_membership_drift(membership, session, actor) -> None:
    """Locked ownership/configuration/placement drift checks for one machine.

    The machine-scope recheck runs first, on the freshly locked row: the
    session lock does not serialize machine client/placement edits, so the
    row reloaded here can have left the actor's scope since the
    all-membership gate ran. A machine the actor no longer holds scope over
    denies ``ACTOR_SCOPE`` before any drift code can disclose its new state;
    ``CLIENT_CHANGED`` / ``PLACEMENT_CHANGED`` remain reserved for machines
    the actor is still authorized over.
    """
    from assets.models import AssetMachine

    machine = AssetMachine.objects.select_for_update().get(pk=membership.machine_id)
    try:
        require_machine_scope(actor, machine)
    except Exception as exc:
        raise ReplayError('ACTOR_SCOPE', str(exc)) from exc
    if machine.client is None or machine.client.code != membership.client_code:
        raise ReplayError('CLIENT_CHANGED', f'{membership.alias} changed client')
    if (
        machine.placement_version != membership.placement_version
        or machine.physical_location_id != membership.placement_id
    ):
        raise ReplayError(
            'PLACEMENT_CHANGED', f'{membership.alias} moved since the claim'
        )
    _reevaluate_ownership(membership)


def replay_batch(session, actor, *, batch_key: str, now=None, values=None):
    """Emit one bounded batch of fresh samples for the allowed aliases.

    ``batch_key`` is the persisted batch identity; re-committing the same batch
    with the same canonical payload — value **and** timestamp/quality — returns
    the stored receipts without new effects. ``values`` optionally overrides
    emitted values per ``(alias, signal)``; by default each binding re-emits
    its current stored value with a genuine new timestamp.
    """
    from machine_health.services.ingestion import ingest_readings

    now = now or timezone.now()
    _require_actor(actor)
    fingerprint.require_postgresql()
    values = values or {}
    with transaction.atomic(), effects.synthetic_effects():
        locked = _locked_session(session)
        # Current scope for every membership first — before the feedable
        # gate and the binding/state/ledger/drift reads: a scope-denied
        # actor receives ACTOR_SCOPE regardless of stopped, expired,
        # claimed, empty or configuration-drifted state, and no status
        # write is even attempted for them.
        scope_error = _session_scope_error(actor, locked)
        if scope_error is not None:
            raise scope_error
        _require_feedable(locked, now)

        allowed = set(replay_aliases(locked))
        bindings = list(
            locked.machines.filter(alias__in=allowed, claim_active=True).order_by(
                'alias'
            )
        )
        if not bindings:
            raise ReplayError('NO_BINDINGS', 'No replay-authorized session machines')
        if len(bindings) > contract.MAX_BINDINGS:
            raise ReplayError(
                'BATCH_TOO_LARGE', 'Replay cohort exceeds the bounded size'
            )

        source_ids = {
            row.source_id
            for row in locked.ledger_objects.filter(
                kind=DemoMetricsObject.Kind.SOURCE, source__isnull=False
            )
        }
        candidates = []
        session_tag_prefix = f'{contract.TAG_NAMESPACE}/{locked.session_key}/'
        for membership in bindings:
            _check_membership_drift(membership, locked, actor)
            machine = membership.machine
            for binding in machine.signal_bindings.filter(
                active=True, source_id__in=source_ids
            ).order_by('pk'):
                source = binding.source
                if (
                    source is None
                    or not source.active
                    or (source.config or {}).get('session_key') != locked.session_key
                    or not (binding.external_key or '').startswith(session_tag_prefix)
                ):
                    raise ReplayError(
                        'CONFIG_DRIFT',
                        f'{membership.alias}/{binding.signal_kind} no longer carries '
                        'session-owned configuration',
                    )
                candidates.append((membership.alias, binding.signal_kind))

        # The drift loop above is read-only and its codes (CLIENT_CHANGED,
        # PLACEMENT_CHANGED) keep their documented precedence for touched
        # machines of an authorized actor — the all-membership scope
        # boundary already ran under the lock above.
        _validate_batch_inputs(batch_key, values, candidates)

        batches: dict[int, list] = {}
        item_payloads = []
        replayed_items = []
        digest_stamp = reference.iso(reference.aware_utc(now))
        for membership in bindings:
            # Re-prove scope immediately before this machine's claims: the
            # identical-receipt readback must not precede the boundary.
            _recheck_scope(membership, actor)
            machine = membership.machine
            for binding in machine.signal_bindings.filter(
                active=True, source_id__in=source_ids
            ).order_by('pk'):
                state = getattr(binding, 'state', None)
                if state is None:
                    continue  # never-seen machines stay never seen (B02)
                current = (state.value or {}).get('value')
                value = values.get((membership.alias, binding.signal_kind), current)
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or value != value
                    or value in (float('inf'), float('-inf'))
                ):
                    raise ReplayError(
                        'BAD_BATCH',
                        f'no finite stored value for {membership.alias}/{binding.signal_kind}',
                    )
                digest = apply_service.request_hash(
                    'replay_observation',
                    {
                        'batch': batch_key,
                        'external_key': binding.external_key,
                        'value': value,
                        # Full canonical payload identity: the emitted
                        # timestamp and quality are part of the operation.
                        'observed_at': digest_stamp,
                        'quality': REPLAY_QUALITY,
                    },
                )
                _receipt, created = apply_service.claim_receipt(
                    session=locked,
                    operation_kind=DemoMetricsReceipt.Operation.REPLAY_OBSERVATION,
                    item_key=f'replay/{batch_key}/{membership.alias}/{binding.signal_kind}',
                    digest=digest,
                    observed_at=_aware(now),
                )
                if not created:
                    # Exact replay of this operation key: stored receipt, no new
                    # effects — the sample is not re-ingested at all.
                    replayed_items.append((membership.alias, binding.signal_kind))
                    continue
                batches.setdefault(binding.source_id, []).append({
                    'external_key': binding.external_key,
                    'value': value,
                    'observed_at': now,
                    'quality': REPLAY_QUALITY,
                })
                item_payloads.append((membership.alias, binding.signal_kind))

        if len(item_payloads) + len(replayed_items) > contract.MAX_OBSERVATIONS:
            raise ReplayError(
                'BATCH_TOO_LARGE', 'Replay batch exceeds the bounded size'
            )

        accepted = 0
        from assets.health_models import HealthSource

        for source_id, payloads in sorted(batches.items()):
            source = HealthSource.objects.get(pk=source_id)
            result = ingest_readings(source, payloads, now=_aware(now))
            if (
                result.accepted != len(payloads)
                or result.unmapped
                or result.rejected
                or result.replayed
            ):
                raise ReplayError(
                    'PARTIAL_BATCH',
                    f'Replay batch {batch_key} was not fully accepted: {result.as_dict()}',
                )
            accepted += result.accepted

        return {
            'batch_key': batch_key,
            'accepted': accepted,
            'replayed': len(replayed_items),
            'aliases': sorted({
                alias for alias, _signal in item_payloads + replayed_items
            }),
        }


def stop_session(session, actor, *, now=None):
    """Revoke future replay authority under the session lock."""
    now = now or timezone.now()
    _require_actor(actor)
    fingerprint.require_postgresql()
    with transaction.atomic(), effects.synthetic_effects():
        locked = _locked_session(session)
        # Current scope first: no status write and not even the
        # already-stopped readback is given to an out-of-scope actor.
        scope_error = _session_scope_error(actor, locked)
        if scope_error is not None:
            raise scope_error
        if locked.status == DemoMetricsSession.Status.STOPPED:
            return {'stopped': False, 'session_key': locked.session_key}
        if locked.status not in (
            DemoMetricsSession.Status.ACTIVE,
            DemoMetricsSession.Status.EXPIRED,
        ):
            raise ReplayError('SESSION_CLEANED', 'A cleaned session cannot be stopped')
        locked.status = DemoMetricsSession.Status.STOPPED
        locked.stopped_at = _aware(now)
        locked.save(update_fields=['status', 'stopped_at'])
        apply_service.claim_receipt(
            session=locked,
            operation_kind=DemoMetricsReceipt.Operation.STOP,
            item_key='stop',
            digest=apply_service.request_hash(
                'stop', {'session': str(locked.pk), **_actor_identity(actor)}
            ),
        )
        return {'stopped': True, 'session_key': locked.session_key}


def run_replay(
    session,
    actor,
    *,
    interval_seconds: float = 30,
    max_duration_seconds: float = 1800,
    now_fn=None,
    sleep_fn=None,
):
    """Run the bounded feed loop once (manual execution, no perpetual schedule).

    Time is injectable for tests; production passes real time. The loop stops at
    the duration bound or on session stop/expiry. Interval and duration are
    bounded. A repeated run never establishes a second active feed: when the
    durable feed claim already exists the loop refuses to emit batches
    (``already_started``) — recover lost responses with explicit
    :func:`replay_batch` receipt replays, not a second feed.
    """
    _require_actor(actor)
    if (
        isinstance(interval_seconds, bool)
        or not isinstance(interval_seconds, (int, float))
        or interval_seconds < 0
        or interval_seconds > MAX_INTERVAL_SECONDS
    ):
        raise ReplayError('BAD_BOUNDS', 'interval_seconds outside the bounded range')
    if (
        isinstance(max_duration_seconds, bool)
        or not isinstance(max_duration_seconds, (int, float))
        or max_duration_seconds <= 0
        or max_duration_seconds > MAX_DURATION_SECONDS
    ):
        raise ReplayError(
            'BAD_BOUNDS', 'max_duration_seconds outside the bounded range'
        )

    now_fn = now_fn or timezone.now
    sleep_fn = sleep_fn or time_module.sleep
    started = now_fn()
    claim = start_feed(session, actor, now=started)
    if claim['already_started']:
        return {
            'batches': 0,
            'started': False,
            'already_started': True,
            'session_key': claim['session_key'],
        }

    batch_index = (
        DemoMetricsReceipt.objects
        .filter(
            session=session,
            operation_kind=DemoMetricsReceipt.Operation.REPLAY_OBSERVATION,
        )
        .exclude(item_key=FEED_CLAIM_KEY)
        .count()
    )
    max_batches = max(1, int(max_duration_seconds / max(interval_seconds, 1)) + 1)
    committed = 0
    while committed < max_batches:
        now = now_fn()
        if (now - started).total_seconds() >= max_duration_seconds:
            break
        batch_index += 1
        try:
            replay_batch(session, actor, batch_key=f'batch-{batch_index}', now=now)
        except ReplayError as exc:
            if exc.code in ('SESSION_STOPPED', 'SESSION_EXPIRED'):
                break
            raise
        committed += 1
        if interval_seconds > 0:
            sleep_fn(interval_seconds)
    return {'batches': committed, 'started': True, 'already_started': False}
