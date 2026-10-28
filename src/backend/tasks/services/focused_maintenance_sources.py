"""Fresh native Work/history reads with exact prepared-source ownership."""

import hashlib
import json
from datetime import UTC, datetime

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied

from assets import ai_read as asset_read
from common.models import Attachment
from repair.models import RepairPacket
from tasks import ai_read as work_read
from tasks.models import WorkOrder
from tasks.services.repair_scope_drafts import (
    _repair_source_work,
    _require_source_scope,
    _source_snapshot,
)
from tasks.services.work_orders import WorkOrderCommandError
from tasks.work_execution.exercise_verification import _evidence_registry
from users.permissions import check_user_role


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(',', ':'), default=str).encode()
    ).hexdigest()


def _authorized(actor_id, work_order_id, machine_id):
    actor = get_user_model().objects.filter(pk=actor_id, is_active=True).first()
    if actor is None or not check_user_role(actor, 'work_order', 'view'):
        raise PermissionDenied('The requested Work sources are unavailable.')
    work = WorkOrder.objects.select_related('machine').get(pk=work_order_id)
    _require_source_scope(actor, work)
    if work.machine_id != machine_id:
        raise PermissionDenied('The requested Work sources are unavailable.')
    packet = RepairPacket.objects.filter(work_order=work, machine_id=machine_id).first()
    stamp = (
        work.lifecycle_version,
        work.updated_at,
        work.machine.updated_at,
        packet.pk if packet else None,
        packet.updated_at if packet else None,
    )
    return actor, work, packet, stamp


def _prepared_e05(work, actor):
    # Resolve by native code, never by a guessed attachment id. The registry
    # verifies source-event actor, owning Work/packet, profile, hash and bytes.
    owned, _, _, packet = _repair_source_work(work.pk, actor)
    evidence = _evidence_registry(owned, codes={'P08-TEMP-E05'}).get('P08-TEMP-E05')
    if evidence is None:
        return {'status': 'unavailable', 'label': 'P08-TEMP-E05'}
    attachment = Attachment.objects.get(pk=evidence['id'])
    registry = (
        owned.events
        .filter(event_type='EXERCISE_EVIDENCE_ATTACHED')
        .order_by('-pk')
        .first()
    )
    expected = (
        ((registry.metadata.get('result_metadata') or {}).get('evidence') or {}).get(
            'P08-TEMP-E05', {}
        )
        if registry
        else {}
    )
    source = _source_snapshot(attachment)
    with attachment.attachment.open('rb') as stored:
        content = stored.read(8193)
    if (
        len(content) > 8192
        or expected.get('id') != attachment.pk
        or source['sha256'] != expected.get('sha256')
        or hashlib.sha256(content).hexdigest() != source['sha256']
    ):
        return {'status': 'unavailable', 'label': 'P08-TEMP-E05'}
    return {
        'status': 'ok',
        **source,
        'content': content.decode('utf-8'),
        'label': 'P08-TEMP-E05',
        'prepared': True,
        'work_order_id': work.pk,
        'machine_id': work.machine_id,
        'repair_packet_id': packet.pk,
        'source_event_id': registry.pk,
        'limitation': 'Prepared exercise source; not a measured field result, completed physical work or operating permission. Current recorded checks are separate.',
    }


def read_focused_maintenance_sources(
    *, actor_id, work_order_id, machine_id, include_history=False, include_e05=False
):
    """Reauthorize every source bundle and discard it if its owner/version changes."""
    if (
        any(
            type(value) is not int or value < 1 for value in (work_order_id, machine_id)
        )
        or type(include_history) is not bool
        or type(include_e05) is not bool
    ):
        raise PermissionDenied('The requested Work sources are unavailable.')
    actor, work, packet, before = _authorized(actor_id, work_order_id, machine_id)
    sources = {}
    reads = []

    def record(name, value):
        sources[name] = value
        receipt = {
            'reader': name,
            'read_at': datetime.now(UTC).isoformat(),
            'work_order_id': work.pk,
            'machine_id': work.machine_id,
            'result_sha256': _digest(value),
            'status': 'unavailable'
            if isinstance(value, dict) and value.get('status') == 'unavailable'
            else 'ok',
        }
        if (
            isinstance(value, dict)
            and value.get('status') == 'ok'
            and 'sha256' in value
        ):
            receipt.update({
                'attachment_id': value.get('id'),
                'source_sha256': value['sha256'],
                'source_event_id': value.get('source_event_id'),
            })
        if isinstance(value, dict) and isinstance(value.get('records'), list):
            receipt.update({
                'returned_count': value.get('returned_count'),
                'history_work_order_ids': [
                    row['work_order_id']
                    for row in value['records']
                    if row.get('work_order_id') is not None
                ],
            })
        reads.append(receipt)

    record(
        'tasks.ai_read.work_order_repair_state',
        work_read.work_order_repair_state(work, machine_authorized=True),
    )
    record('tasks.ai_read.work_order_closeout', work_read.work_order_closeout(work))
    if include_history:
        record(
            'assets.ai_read.machine_maintenance_history',
            asset_read.machine_maintenance_history(actor, work.machine, limit=10),
        )
    if include_e05:
        try:
            e05 = _prepared_e05(work, actor)
        except (WorkOrderCommandError, OSError, ValueError, UnicodeError):
            e05 = {'status': 'unavailable', 'label': 'P08-TEMP-E05'}
        record('tasks.services.focused_maintenance_sources._prepared_e05', e05)
    _, _, _, after = _authorized(actor_id, work_order_id, machine_id)
    if after != before:
        raise PermissionDenied('The requested Work sources changed during the read.')
    return {
        'status': 'ok',
        'work_order_id': work.pk,
        'machine_id': work.machine_id,
        'lifecycle_version': work.lifecycle_version,
        'repair_packet_id': packet.pk if packet else None,
        'packet_revision': packet.updated_at.isoformat() if packet else None,
        'sources': sources,
        'source_reads': reads,
    }
