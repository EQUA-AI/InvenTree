"""Supervisor verification and governed closeout amendments (Feature #15).

Completed closeouts are immutable; verification is the sole one-shot carveout
(previously-null ``verified_by``/``verified_at`` only), and every correction is
an append-only ``CloseoutAmendment`` whose approved effective projection is
overlaid by readers without touching the original row (FR-CO-013/014).
"""

import hashlib
import json
import uuid

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from tasks.closeout_models import (
    CloseoutAmendment,
    CloseoutAmendmentStatus,
    CloseoutEffect,
)
from tasks.permissions import require_permission
from tasks.services.work_orders import (
    WorkOrderCommandError,
    _append_result,
    _canonical_hash,
    _locked_work_order,
    _replay_or_none,
    _require_scope,
    _require_version,
)

VERIFY_CLOSEOUT = 'tasks.verify_closeout'
AMEND_CLOSEOUT = 'tasks.amend_closeout'

AMENDABLE_FIELDS = (
    'cause',
    'action',
    'result',
    'verification_summary',
    'downtime_minutes',
    'follow_up_required',
    'follow_up',
)


class VerificationError(WorkOrderCommandError):
    """The verification command could not be applied."""

    code = 'CLOSEOUT_VERIFY_INVALID'


class AmendmentError(WorkOrderCommandError):
    """The amendment command could not be applied."""

    code = 'AMENDMENT_INVALID'


class AmendmentPolicyRequired(WorkOrderCommandError):  # noqa: N818 - established command error name
    """Deployment policy routes this amendment through approvals."""

    code = 'AMENDMENT_POLICY_REQUIRED'


def _snapshot_hash(snapshot) -> str:
    return hashlib.sha256(
        json.dumps(
            snapshot, sort_keys=True, separators=(',', ':'), default=str
        ).encode()
    ).hexdigest()


def _closeout_for(work_order):
    closeout = getattr(work_order, 'structured_closeout', None)
    if closeout is None:
        raise AmendmentError('The work order has no structured closeout')
    return closeout


def base_closeout_fields(closeout) -> dict:
    """The original completed field values, as a plain dict."""
    return {name: getattr(closeout, name) for name in AMENDABLE_FIELDS}


def applied_amendments(closeout) -> list:
    """Applied amendments, newest first, honouring a ``to_attr`` prefetch.

    List endpoints avoid a query per row with
    ``Prefetch('...amendments', queryset=<applied, newest first>,
    to_attr='applied_amendments')``; single-object callers fall back to one
    query here.
    """
    prefetched = getattr(closeout, 'applied_amendments', None)
    if prefetched is not None:
        return list(prefetched)
    return list(
        closeout.amendments.filter(status=CloseoutAmendmentStatus.APPLIED).order_by(
            '-applied_at', '-pk'
        )
    )


def _effective_fields(closeout, applied) -> dict:
    fields = base_closeout_fields(closeout)
    if applied and applied[0].effective_snapshot:
        fields.update(applied[0].effective_snapshot.get('closeout', {}))
    return fields


def effective_closeout(closeout) -> dict:
    """Overlay the latest applied amendment onto the immutable original."""
    return _effective_fields(closeout, applied_amendments(closeout))


def effective_closeout_overview(closeout) -> dict:
    """Effective field values plus ``amended``/``amendment_count`` provenance.

    The REST projection helper: read surfaces must show the governed
    correction rather than the superseded original, and must say that a
    correction happened rather than changing values silently.
    """
    applied = applied_amendments(closeout)
    fields = _effective_fields(closeout, applied)
    fields['amended'] = bool(applied)
    fields['amendment_count'] = len(applied)
    return fields


@transaction.atomic
def verify_closeout(
    *,
    work_order_id,
    actor,
    expected_version,
    idempotency_key,
    correlation_id=None,  # gitleaks:allow
):
    """One-shot supervisor verification of a completed closeout."""
    policy = str(getattr(settings, 'AIMMS_CLOSEOUT_VERIFY_POLICY', 'off'))
    if policy == 'off':
        raise VerificationError('Closeout verification is disabled by policy')
    work_order = _locked_work_order(work_order_id)
    payload = {'work_order_id': work_order_id, 'expected_version': expected_version}
    request_hash = _canonical_hash('closeout_verify', actor, payload)
    replay = _replay_or_none(work_order, idempotency_key, request_hash)
    if replay:
        return replay

    _require_version(work_order, expected_version)
    require_permission(actor, VERIFY_CLOSEOUT)
    _require_scope(actor, work_order)
    closeout = _closeout_for(work_order)
    separation = bool(getattr(settings, 'AIMMS_CLOSEOUT_VERIFY_SEPARATION', True))
    if separation and closeout.completed_by_id == actor.pk:
        raise VerificationError('The completer cannot verify their own closeout')
    if closeout.verified_by_id is not None:
        raise VerificationError('The closeout is already verified')

    closeout.verified_by = actor
    closeout.verified_at = timezone.now()
    closeout.save(update_fields=['verified_by', 'verified_at'])
    return _append_result(
        work_order=work_order,
        actor=actor,
        command='closeout_verify',
        event_type='CLOSEOUT_VERIFIED',
        from_status=work_order.lifecycle_status,
        to_status=work_order.lifecycle_status,
        reason='',
        correlation_id=correlation_id or uuid.uuid4(),
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        result_metadata={'closeout_id': closeout.pk},
    )


def _normalize_changes(closeout, changes) -> dict:
    if not isinstance(changes, dict) or not changes:
        raise AmendmentError('An amendment requires at least one field change')
    effective = effective_closeout(closeout)
    normalized = {}
    for field, change in changes.items():
        if field not in AMENDABLE_FIELDS:
            raise AmendmentError(f'Field {field!r} cannot be amended')
        new_value = change.get('to') if isinstance(change, dict) else change
        normalized[field] = {'from': effective.get(field), 'to': new_value}
    return normalized


@transaction.atomic
def propose_amendment(
    *,
    work_order_id,
    actor,
    changes,
    reason,
    expected_version,
    idempotency_key,
    correlation_id=None,
):
    """Propose an append-only correction to a completed closeout."""
    if not getattr(settings, 'AIMMS_CLOSEOUT_AMENDMENTS_ENABLED', False):
        raise AmendmentError('Closeout amendments are disabled in this deployment')
    work_order = _locked_work_order(work_order_id)
    payload = {
        'work_order_id': work_order_id,
        'expected_version': expected_version,
        'changes': changes,
        'reason': reason,
    }
    request_hash = _canonical_hash('closeout_amend_propose', actor, payload)
    replay = _replay_or_none(work_order, idempotency_key, request_hash)
    if replay:
        return replay

    _require_version(work_order, expected_version)
    require_permission(actor, AMEND_CLOSEOUT)
    _require_scope(actor, work_order)
    closeout = _closeout_for(work_order)
    if not (reason or '').strip():
        raise AmendmentError('An amendment requires a reason')
    normalized = _normalize_changes(closeout, changes)

    amendment = CloseoutAmendment.objects.create(
        closeout=closeout,
        changes=normalized,
        base_content_hash=closeout.content_hash,
        reason=reason,
        requested_by=actor,
    )
    return _append_result(
        work_order=work_order,
        actor=actor,
        command='closeout_amend_propose',
        event_type='CLOSEOUT_AMENDMENT_PROPOSED',
        from_status=work_order.lifecycle_status,
        to_status=work_order.lifecycle_status,
        reason=reason,
        correlation_id=correlation_id or uuid.uuid4(),
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        result_metadata={'amendment_id': amendment.pk},
    )


def _asset_history_projection(work_order, fields) -> dict:
    parts = []
    if fields.get('cause'):
        parts.append(f'Cause: {fields["cause"]}')
    parts.append(f'Action: {fields["action"]}')
    parts.append(f'Result: {fields["result"]}')
    parts.append(f'Verification: {fields["verification_summary"]}')
    return {
        'summary': (work_order.title or fields['action'])[:255],
        'details': '\n'.join(parts),
    }


def _apply_amendment(work_order, closeout, amendment, actor):
    # No staleness check here on purpose: completed closeouts are immutable
    # (save() rejects every mutation outside verification), so
    # ``amendment.base_content_hash`` — stamped from the row at proposal —
    # can never diverge from ``closeout.content_hash``. The stamp is kept
    # as provenance only.
    fields = effective_closeout(closeout)
    for field, change in amendment.changes.items():
        fields[field] = change.get('to')
    for name in ('action', 'result', 'verification_summary'):
        if not str(fields.get(name, '') or '').strip():
            raise AmendmentError(f'Amendment cannot blank required field {name!r}')

    snapshot = {
        'closeout': fields,
        'asset_history': _asset_history_projection(work_order, fields),
    }
    amendment.effective_snapshot = snapshot
    amendment.effective_snapshot_hash = _snapshot_hash(snapshot)
    amendment.status = CloseoutAmendmentStatus.APPLIED
    amendment.decided_by = actor
    amendment.applied_at = timezone.now()
    amendment.save(
        update_fields=[
            'effective_snapshot',
            'effective_snapshot_hash',
            'status',
            'decided_by',
            'applied_at',
        ]
    )
    if getattr(settings, 'AIMMS_CLOSEOUT_WIZARD_ENABLED', False):
        CloseoutEffect.objects.get_or_create(
            effect_key=f'closeout:{closeout.pk}:notification:amendment:{amendment.pk}',
            defaults={
                'closeout': closeout,
                'effect_type': 'notification',
                'payload_hash': amendment.effective_snapshot_hash,
            },
        )


@transaction.atomic
def decide_amendment(
    *,
    work_order_id,
    amendment_id,
    actor,
    approve,
    expected_version,
    idempotency_key,
    reason='',
    correlation_id=None,
):
    """Approve (and apply) or reject one proposed amendment under policy."""
    if not getattr(settings, 'AIMMS_CLOSEOUT_AMENDMENTS_ENABLED', False):
        raise AmendmentError('Closeout amendments are disabled in this deployment')
    work_order = _locked_work_order(work_order_id)
    payload = {
        'work_order_id': work_order_id,
        'amendment_id': amendment_id,
        'expected_version': expected_version,
        'approve': bool(approve),
        'reason': reason,
    }
    request_hash = _canonical_hash('closeout_amend_decide', actor, payload)
    replay = _replay_or_none(work_order, idempotency_key, request_hash)
    if replay:
        return replay

    _require_version(work_order, expected_version)
    _require_scope(actor, work_order)
    closeout = _closeout_for(work_order)
    amendment = (
        CloseoutAmendment.objects
        .select_for_update()
        .filter(pk=amendment_id, closeout=closeout)
        .first()
    )
    if amendment is None:
        raise AmendmentError('Amendment does not belong to this work order')
    if amendment.status != CloseoutAmendmentStatus.PROPOSED:
        raise AmendmentError(f'A {amendment.status} amendment cannot be decided')

    policy = str(getattr(settings, 'AIMMS_CLOSEOUT_AMENDMENT_APPROVAL', 'supervisor'))
    if policy == 'approvals':
        raise AmendmentPolicyRequired(
            'This deployment routes amendments through the approvals system'
        )
    if policy == 'supervisor':
        require_permission(actor, VERIFY_CLOSEOUT)
        if amendment.requested_by_id == actor.pk:
            raise AmendmentError('The requester cannot decide their own amendment')
    else:
        require_permission(actor, AMEND_CLOSEOUT)

    if approve:
        _apply_amendment(work_order, closeout, amendment, actor)
        event_type = 'AMENDED'
    else:
        amendment.status = CloseoutAmendmentStatus.REJECTED
        amendment.decided_by = actor
        amendment.save(update_fields=['status', 'decided_by'])
        event_type = 'CLOSEOUT_AMENDMENT_REJECTED'

    return _append_result(
        work_order=work_order,
        actor=actor,
        command='closeout_amend_decide',
        event_type=event_type,
        from_status=work_order.lifecycle_status,
        to_status=work_order.lifecycle_status,
        reason=reason,
        correlation_id=correlation_id or uuid.uuid4(),
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        result_metadata={
            'amendment_id': amendment.pk,
            'amendment_status': amendment.status,
        },
    )
