"""Signed, freshly authorized Work focus for the existing chat repository."""

from __future__ import annotations

import hashlib
import json

from django.contrib.auth import get_user_model
from django.core import signing

from tasks.scope import scope_for_work_order, scoped_work_orders

from ai.core.integrations.retrieval_authority import fresh_role

FOCUS_SCHEMA_VERSION = 1
FOCUS_RECEIPT_MAX_AGE = 300
WORK_FOCUS_SNAPSHOT_KEY = 'work_focus_snapshot'
_SALT = 'aimms.chat.work-focus.v1'


class WorkFocusError(ValueError):
    """Reject unavailable, unauthorized, stale or invalid focus uniformly."""


def authorized_focus(actor_id, work_order_id, request_id=None) -> dict:
    """Resolve current native Work identity without cached actor permissions."""
    if type(work_order_id) is not int or work_order_id < 1:
        raise WorkFocusError('work_focus_rejected')
    actor = get_user_model().objects.filter(pk=actor_id, is_active=True).first()
    if actor is None or not fresh_role(actor, 'work_order'):
        raise WorkFocusError('work_focus_rejected')
    work_order = (
        scoped_work_orders(actor)
        .select_related('machine')
        .filter(pk=work_order_id)
        .first()
    )
    if work_order is None:
        raise WorkFocusError('work_focus_rejected')
    boundary = scope_for_work_order(work_order)
    boundary_json = json.dumps(
        {
            'client_id': boundary.client_id,
            'customer_id': boundary.customer_id,
            'site_key': boundary.site_key,
        },
        sort_keys=True,
        separators=(',', ':'),
    )
    focus = {
        'schema_version': FOCUS_SCHEMA_VERSION,
        'kind': 'work_order',
        'work_order_id': work_order.pk,
        'lifecycle_version': work_order.lifecycle_version,
        'machine_id': work_order.machine_id,
        'boundary_hash': hashlib.sha256(boundary_json.encode()).hexdigest(),
    }

    if request_id is not None:
        from tasks.investigation_models import InvestigationRequest

        if type(request_id) is not int or request_id < 1:
            raise WorkFocusError('work_focus_rejected')
        request = InvestigationRequest.objects.filter(
            pk=request_id, work_order=work_order
        ).first()
        if request is None:
            raise WorkFocusError('work_focus_rejected')
        focus.update(
            schema_version=2, request_id=request.pk, request_version=request.version
        )
    return focus


#: The identity a turn's result, its reloaded message and the model may carry.
#: The hashed maintenance boundary stays server-side: it exists to detect a
#: changed boundary, not to describe the record.
PUBLIC_FOCUS_KEYS = (
    'schema_version',
    'kind',
    'work_order_id',
    'lifecycle_version',
    'machine_id',
)


#: How a saved answer's Work focus stands now, for the owner reading it back.
#: ``current``: still visible and unchanged; ``changed``: still visible, but
#: its lifecycle version, machine or maintenance boundary moved since the
#: question; ``unavailable``: the reader can no longer see that Work.
WORK_FOCUS_STATES = ('current', 'changed', 'unavailable')


def public_focus(snapshot) -> dict | None:
    """Project a verified snapshot to its ids, or None when there is none."""
    if not isinstance(snapshot, dict):
        return None
    keys = (
        (*PUBLIC_FOCUS_KEYS, 'request_id', 'request_version')
        if snapshot.get('schema_version') == 2
        else PUBLIC_FOCUS_KEYS
    )
    return {key: snapshot.get(key) for key in keys}


def issue_receipt(
    actor_id,
    scope_hash,
    thread_id,
    work_order_id,
    expected_version,
    *,
    request_id=None,
    request_version=None,
):
    """Acknowledge an exact lifecycle identity, never a grant or write intent."""
    if type(expected_version) is not int or expected_version < 1:
        raise WorkFocusError('work_focus_rejected')
    if (request_id is None) != (request_version is None):
        raise WorkFocusError('work_focus_rejected')
    if request_version is not None and (
        type(request_version) is not int or request_version < 1
    ):
        raise WorkFocusError('work_focus_rejected')
    focus = authorized_focus(actor_id, work_order_id, request_id)
    if request_id is not None and focus['request_version'] != request_version:
        raise WorkFocusError('work_focus_rejected')
    if focus['lifecycle_version'] != expected_version:
        raise WorkFocusError('work_focus_rejected')
    receipt = signing.dumps(
        {
            'actor_id': str(actor_id),
            'scope_hash': scope_hash,
            'thread_id': thread_id,
            'focus': focus,
        },
        salt=_SALT,
    )
    return {
        'schema_version': FOCUS_SCHEMA_VERSION,
        'thread_id': thread_id,
        'focus': focus,
        'receipt': receipt,
        'expires_in': FOCUS_RECEIPT_MAX_AGE,
    }


def resolve_receipt(actor_id, scope_hash, thread_id, receipt) -> dict:
    """Verify binding and expiry, then reauthorize the exact live Work row."""
    if not isinstance(receipt, str) or not receipt or len(receipt) > 4096:
        raise WorkFocusError('work_focus_rejected')
    try:
        claims = signing.loads(receipt, salt=_SALT, max_age=FOCUS_RECEIPT_MAX_AGE)
    except (signing.BadSignature, ValueError, TypeError):
        raise WorkFocusError('work_focus_rejected') from None
    if (
        not isinstance(claims, dict)
        or set(claims) != {'actor_id', 'scope_hash', 'thread_id', 'focus'}
        or claims['actor_id'] != str(actor_id)
        or claims['scope_hash'] != scope_hash
        or claims['thread_id'] != thread_id
        or not isinstance(claims['focus'], dict)
    ):
        raise WorkFocusError('work_focus_rejected')
    current = authorized_focus(
        actor_id,
        claims['focus'].get('work_order_id'),
        claims['focus'].get('request_id'),
    )
    if claims['focus'] != current:
        raise WorkFocusError('work_focus_rejected')
    return current
