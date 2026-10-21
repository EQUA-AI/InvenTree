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


def authorized_focus(
    actor_id, work_order_id, request_id=None, component=None, evidence=None
) -> dict:
    """Resolve current native Work identity without cached actor permissions."""
    if work_order_id is None and component is not None and request_id is None:
        return extended_focus(actor_id, None, component, evidence)
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
    return (
        extended_focus(actor_id, focus, component, evidence)
        if component is not None or evidence is not None
        else focus
    )


def reading_revision(reading):
    """Pin all native reading facts, including disposition and evidence links."""
    from decimal import Decimal

    fields = {}
    for field in reading._meta.fields:
        value = getattr(reading, field.attname)
        if value is not None and field.get_internal_type() == 'DecimalField':
            value = format(
                Decimal(str(value)).quantize(Decimal(1).scaleb(-field.decimal_places)),
                'f',
            )
        fields[field.attname] = str(value)
    # Sorted here rather than in SQL so a list read's prefetch is reused.
    fields['evidence'] = [
        link.attachment_id
        for link in sorted(reading.evidence_links.all(), key=lambda link: link.pk)
    ]
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


def extended_focus(actor_id, focus, component, evidence):
    """Re-read exact native references with fresh roles and owner scope."""
    from tasks.scope import ScopeError, require_machine_scope

    from assets.equipment_positions import REFERENCE_POSITIONS_RULE, position_reference
    from assets.models import AssetMachine

    actor = get_user_model().objects.filter(pk=actor_id, is_active=True).first()
    if actor is None or not fresh_role(actor, 'work_order'):
        raise WorkFocusError('work_focus_rejected')
    result = (
        dict(focus)
        if focus
        else {
            'kind': 'reference_position',
            'work_order_id': None,
            'lifecycle_version': None,
            'machine_id': None,
        }
    )
    result['schema_version'] = 3
    if component is not None:
        if not isinstance(component, dict) or set(component) != {
            'machine_id',
            'rule',
            'position_id',
            'slot_code',
            'revision',
        }:
            raise WorkFocusError('work_focus_rejected')
        mid = component['machine_id']
        if (
            type(mid) is not int
            or mid < 1
            or component['rule'] != REFERENCE_POSITIONS_RULE
            or not isinstance(component['position_id'], str)
            or (
                component['slot_code'] is not None
                and not isinstance(component['slot_code'], str)
            )
        ):
            raise WorkFocusError('work_focus_rejected')
        machine = AssetMachine.objects.filter(pk=mid, active=True).first()
        if machine is None or (focus and focus['machine_id'] != mid):
            raise WorkFocusError('work_focus_rejected')
        try:
            require_machine_scope(actor, machine)
            current = position_reference(
                machine, component['position_id'], component['slot_code']
            )
        except (ScopeError, ValueError):
            raise WorkFocusError('work_focus_rejected') from None
        result['machine_id'] = mid
        result['component'] = current
    if evidence is not None:
        result['evidence'] = evidence_reference(actor, evidence, result)
    return result


def evidence_reference(actor, candidate, focus):
    """Resolve existing readable evidence; focus never promotes chat files."""
    from tasks.closeout_models import CloseoutReading

    from common.api import _maintenance_attachment_scope
    from common.attachment_revision import attachment_revision
    from common.models import Attachment

    if (
        not isinstance(candidate, dict)
        or set(candidate) != {'kind', 'id', 'revision'}
        or type(candidate['id']) is not int
        or candidate['id'] < 1
    ):
        raise WorkFocusError('work_focus_rejected')
    kind, pk = candidate['kind'], candidate['id']
    if not isinstance(kind, str):
        raise WorkFocusError('work_focus_rejected')
    if kind == 'closeout_reading':
        from django.conf import settings

        if not getattr(settings, 'AIMMS_CLOSEOUT_WIZARD_ENABLED', False):
            raise WorkFocusError('work_focus_rejected')
        reading = CloseoutReading.objects.filter(
            pk=pk, work_order_id=focus.get('work_order_id')
        ).first()
        if reading is None or focus.get('work_order_id') is None:
            raise WorkFocusError('work_focus_rejected')
        revision = reading_revision(reading)
    elif kind in {'attachment', 'media'}:
        attachment = _maintenance_attachment_scope(
            Attachment.objects.filter(pk=pk), actor
        ).first()
        if attachment is None:
            raise WorkFocusError('work_focus_rejected')
        # An exact evidence item belongs to the named Work or machine; an
        # unrelated readable item cannot silently become this Work's evidence.
        if (attachment.model_type, attachment.model_id) not in {
            ('workorder', focus.get('work_order_id')),
            ('assetmachine', focus.get('machine_id')),
        }:
            raise WorkFocusError('work_focus_rejected')
        if not attachment.check_permission('view', actor):
            raise WorkFocusError('work_focus_rejected')
        revision = attachment_revision(attachment)
        if kind == 'media':
            revision = indexed_media_revision(attachment)
    else:
        raise WorkFocusError('work_focus_rejected')
    return {'kind': kind, 'id': pk, 'revision': revision}


def indexed_media_revision(attachment):
    """Confirm an indexed source with bounded metadata reads, never file bytes.

    The ingestion stamp anchors size/mtime to the indexed SHA. Missing or
    drifted metadata is unavailable until ingestion confirms it again. Actual
    media serving still hashes the same open handle it serves.
    """
    from django.core.files.storage import default_storage

    from aichat.media_stream import _CONTENT_TYPES
    from aichat.models import AttachmentIngest
    from aichat.services.attachment_ingestion import STAMP_VERSION, storage_mtime

    ingest = (
        AttachmentIngest.objects
        .filter(attachment_id=attachment.pk)
        .order_by('-pk')
        .first()
    )
    metadata = attachment.metadata
    stamp = metadata.get('ai_ingest') if isinstance(metadata, dict) else None
    name = attachment.attachment.name or ''
    suffix = ('.' + name.rsplit('.', 1)[-1].lower()) if '.' in name else ''
    if (
        ingest is None
        or ingest.state != 'indexed'
        or ingest.pipeline not in {'doc', 'image', 'video'}
        or (suffix in {'.md', '.markdown'} and ingest.pipeline != 'doc')
        or (ingest.model_type, ingest.model_id)
        != (attachment.model_type, attachment.model_id)
        or not isinstance(stamp, dict)
        or stamp.get('v') != STAMP_VERSION
        or stamp.get('state') != 'indexed'
        or stamp.get('sha') != ingest.source_sha256
        or stamp.get('name') != name
        or type(stamp.get('size')) is not int
        or stamp['size'] < 0
        or not isinstance(stamp.get('mtime'), str)
        or not stamp['mtime']
        or suffix not in _CONTENT_TYPES
    ):
        raise WorkFocusError('work_focus_rejected')
    try:
        confirmed = (
            default_storage.size(name) == stamp['size']
            and storage_mtime(attachment) == stamp['mtime']
        )
    except Exception:
        raise WorkFocusError('work_focus_rejected') from None
    if not confirmed:
        raise WorkFocusError('work_focus_rejected')
    return ingest.source_sha256


def focus_subject(snapshot):
    """The Work/request (or standalone machine) independent of its references."""
    subject = {
        key: value
        for key, value in snapshot.items()
        if key not in {'component', 'evidence'}
    }
    if subject.get('work_order_id') is not None:
        subject['schema_version'] = 2 if subject.get('request_id') is not None else 1
    return subject


def authorized_subject(actor_id, snapshot):
    """Recheck the parent subject without requiring child evidence to survive."""
    if snapshot.get('work_order_id') is not None:
        return authorized_focus(
            actor_id, snapshot['work_order_id'], snapshot.get('request_id')
        )
    from tasks.scope import ScopeError, require_machine_scope

    from assets.models import AssetMachine

    actor = get_user_model().objects.filter(pk=actor_id, is_active=True).first()
    machine = AssetMachine.objects.filter(
        pk=snapshot.get('machine_id'), active=True
    ).first()
    if (
        snapshot.get('kind') != 'reference_position'
        or actor is None
        or machine is None
        or not fresh_role(actor, 'work_order')
    ):
        raise WorkFocusError('work_focus_rejected')
    try:
        require_machine_scope(actor, machine)
    except ScopeError:
        raise WorkFocusError('work_focus_rejected') from None
    return focus_subject(snapshot)


def refresh_focus(actor_id, snapshot):
    """Resolve every coordinate of a saved focus through the same authority."""
    return authorized_focus(
        actor_id,
        snapshot.get('work_order_id'),
        snapshot.get('request_id'),
        snapshot.get('component'),
        snapshot.get('evidence'),
    )


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
    if snapshot.get('schema_version') == 3:
        keys = (
            *PUBLIC_FOCUS_KEYS,
            *(
                key
                for key in ('request_id', 'request_version', 'component', 'evidence')
                if key in snapshot
            ),
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
    component=None,
    evidence=None,
):
    """Acknowledge an exact lifecycle identity, never a grant or write intent."""
    if work_order_id is not None and (
        type(expected_version) is not int or expected_version < 1
    ):
        raise WorkFocusError('work_focus_rejected')
    if (request_id is None) != (request_version is None):
        raise WorkFocusError('work_focus_rejected')
    if request_version is not None and (
        type(request_version) is not int or request_version < 1
    ):
        raise WorkFocusError('work_focus_rejected')
    focus = authorized_focus(actor_id, work_order_id, request_id, component, evidence)
    if request_id is not None and focus['request_version'] != request_version:
        raise WorkFocusError('work_focus_rejected')
    if (
        focus['lifecycle_version'] != expected_version
        or (component is not None and focus.get('component') != component)
        or (evidence is not None and focus.get('evidence') != evidence)
    ):
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
    current = refresh_focus(actor_id, claims['focus'])
    if claims['focus'] != current:
        raise WorkFocusError('work_focus_rejected')
    return current


class FocusStates(dict):
    """Parent Work/request states with independent reference states."""

    def __init__(self):
        """Keep reference detail local to this read, never persisted."""
        super().__init__()
        self.references = {}


def reference_states(actor_id, snapshot):
    """Check each reference independently of the parent subject state."""
    from tasks.scope import ScopeError, require_machine_scope

    from assets.models import AssetMachine

    states = {}
    for key in ('component', 'evidence'):
        if key not in snapshot:
            continue
        try:
            if key == 'component':
                current = authorized_focus(actor_id, None, component=snapshot[key])[
                    'component'
                ]
            elif snapshot.get('work_order_id') is not None:
                current = authorized_focus(
                    actor_id, snapshot['work_order_id'], evidence=snapshot[key]
                )['evidence']
            else:
                actor = (
                    get_user_model().objects.filter(pk=actor_id, is_active=True).first()
                )
                machine = AssetMachine.objects.filter(
                    pk=snapshot.get('machine_id'), active=True
                ).first()
                if (
                    actor is None
                    or machine is None
                    or not fresh_role(actor, 'work_order')
                ):
                    raise WorkFocusError('work_focus_rejected')
                require_machine_scope(actor, machine)
                current = evidence_reference(actor, snapshot[key], snapshot)
            states[key] = 'current' if current == snapshot[key] else 'changed'
        except (WorkFocusError, ScopeError):
            states[key] = 'unavailable'
    return states
