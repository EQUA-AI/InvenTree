"""Source-linked proposed repair scopes with native command receipts."""

import hashlib
import re
import uuid
from pathlib import PurePosixPath

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q

from assets.health_models import MachineAnomaly
from common.models import Attachment
from repair.models import PacketStatus, RepairPacket
from tasks.models import WorkOrder, WorkOrderEvent, WorkOrderLifecycle
from tasks.scope import ScopeError, require_machine_scope
from tasks.services.work_orders import (
    WorkOrderCommandError,
    WorkOrderScopeError,
    _append_result,
    _canonical_hash,
    _locked_work_order,
    _replay_or_none,
    _require_scope,
    _require_version,
)
from tasks.work_execution.exercise_verification import (
    _evidence_registry,
    exercise_source,
)
from users.permissions import check_user_role

EVENT_TYPE = 'REPAIR_SCOPE_DRAFT_SAVED'
COMMAND = 'save_repair_scope_draft'
_MAX_SOURCE_BYTES = 50 * 1024 * 1024
_GATES = {
    'supervisor_review': 'pending',
    'site_safety_authorization': 'pending',
    'physical_execution': 'not_started',
    'post_repair_verification': 'not_started',
}
_SCOPE_FIELDS = {
    'suspected_cause',
    'steps',
    'tools_and_supplies',
    'required_ppe',
    'isolation_checks',
    'post_work_checks',
    'replacement_parts',
    'parts_decision',
    'open_questions',
}


class RepairScopeDraftError(WorkOrderCommandError):
    """The proposed draft lacks valid case-bound evidence or content."""

    code = 'INVALID_REPAIR_SCOPE_DRAFT'


def _integer(value, name):
    if type(value) is not int or value < 1:
        raise RepairScopeDraftError(f'{name} must be a positive integer.')
    return value


def _text(value, name, maximum=2000):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise RepairScopeDraftError(f'{name} must contain bounded text.')
    return value.strip()


def _scope(value):
    if not isinstance(value, dict) or set(value) != _SCOPE_FIELDS:
        raise RepairScopeDraftError('Supply exactly the proposed scope fields.')
    result = {
        name: _text(value[name], name) for name in ('suspected_cause', 'parts_decision')
    }
    for name in (
        'steps',
        'tools_and_supplies',
        'required_ppe',
        'isolation_checks',
        'post_work_checks',
        'open_questions',
    ):
        items = value[name]
        if not isinstance(items, list) or not 1 <= len(items) <= 20:
            raise RepairScopeDraftError(f'{name} requires one to twenty items.')
        result[name] = [_text(item, name, 1000) for item in items]
    if value['replacement_parts'] != []:
        raise RepairScopeDraftError(
            'This evidence-backed proposal requires zero parts.'
        )
    result['replacement_parts'] = []
    return result


def _actor(actor, *, change=False):
    user = (
        get_user_model()
        .objects.filter(pk=getattr(actor, 'pk', None), is_active=True)
        .first()
    )
    if user is None or (change and not check_user_role(user, 'work_order', 'change')):
        raise PermissionDenied('An active authorized work-order actor is required.')
    return user


def _source_snapshot(attachment):
    if not attachment.attachment:
        raise RepairScopeDraftError('The linked source file is unavailable.')
    digest = hashlib.sha256()
    size = 0
    prefix = b''
    try:
        with attachment.attachment.open('rb') as source:
            while chunk := source.read(65536):
                if not prefix:
                    prefix = chunk[:5]
                size += len(chunk)
                if size > _MAX_SOURCE_BYTES:
                    raise RepairScopeDraftError('The linked source file is too large.')
                digest.update(chunk)
    except (OSError, ValueError) as exc:
        raise RepairScopeDraftError('The linked source file cannot be read.') from exc
    if size == 0:
        raise RepairScopeDraftError('The linked source file is empty.')
    filename = PurePosixPath(attachment.attachment.name).name
    if PurePosixPath(filename).suffix.lower() == '.pdf' and prefix != b'%PDF-':
        raise RepairScopeDraftError('The attached manual is not a PDF document.')
    return {
        'id': attachment.pk,
        'title': attachment.comment or filename,
        'filename': filename,
        'sha256': digest.hexdigest(),
    }


def _snapshot(event):
    data = event.metadata['result_metadata']['draft']
    return {
        **data,
        'id': event.pk,
        'actor_id': event.actor_id,
        'saved_at': event.created_at.isoformat(),
        'lifecycle_version': event.metadata['lifecycle_version'],
    }


def _require_source_scope(actor, work):
    _require_scope(actor, work)
    if work.machine is None:
        raise RepairScopeDraftError('The work order has no equipment context.')
    try:
        require_machine_scope(actor, work.machine)
    except ScopeError as exc:
        raise WorkOrderScopeError(str(exc)) from exc


def read_repair_scope_draft(work_order_id, actor):
    """Read the newest saved proposal after current actor/scope authorization."""
    actor = _actor(actor)
    work = WorkOrder.objects.select_related('machine').get(
        pk=_integer(work_order_id, 'work_order_id')
    )
    _require_source_scope(actor, work)
    packet = RepairPacket.objects.filter(work_order=work).first()
    event = work.events.filter(event_type=EVENT_TYPE).order_by('-pk').first()
    return {
        'work_order_id': work.pk,
        'machine_id': work.machine_id,
        'repair_packet_id': packet.pk if packet else None,
        'lifecycle_version': work.lifecycle_version,
        'draft': _snapshot(event) if event else None,
    }


def _repair_source_work(work_order_id, actor):
    actor = _actor(actor)
    work = WorkOrder.objects.select_related('machine').get(
        pk=_integer(work_order_id, 'work_order_id')
    )
    _require_source_scope(actor, work)
    source = exercise_source(work)
    if source is None:
        raise RepairScopeDraftError('The owning prepared source event is required.')
    binding = source.bindings.select_related('dictionary_point__component').get(
        pk=source.metrics['binding_id']
    )
    component = binding.dictionary_point.component
    packet = RepairPacket.objects.get(work_order=work, machine_id=work.machine_id)
    return work, source, component, packet


def related_work_manual_context(work_order_id, actor):
    """Read owned manual identities without reading inspection or later evidence."""
    work, source, component, packet = _repair_source_work(work_order_id, actor)
    allowed = (
        Q(model_type='assetmachine', model_id=work.machine_id)
        | Q(model_type='assetcomponent', model_id=component.pk)
        | Q(model_type='repairpacket', model_id=packet.pk)
        | Q(model_type='kanbancard', model_id=work.pk)
    )
    manuals = []
    for attachment in Attachment.objects.filter(allowed).order_by('pk'):
        if PurePosixPath(attachment.attachment.name).suffix.lower() != '.pdf':
            continue
        snapshot = _source_snapshot(attachment)
        snapshot['applicability'] = 'related_reference_unverified'
        manuals.append(snapshot)
    return {
        'work_order_id': work.pk,
        'machine_id': work.machine_id,
        'repair_packet_id': packet.pk,
        'source_event_id': source.pk,
        'component_code': component.code,
        'manuals': manuals,
    }


def read_prepared_work_inspection(work_order_id, actor):
    """Read only source-owned E03; never consume prepared post-repair E05/E06."""
    work, source, component, packet = _repair_source_work(work_order_id, actor)
    evidence = _evidence_registry(work, codes={'P08-TEMP-E03'}).get('P08-TEMP-E03')
    if evidence is None:
        raise RepairScopeDraftError('The original prepared E03 source is unavailable.')
    e03_attachment = Attachment.objects.get(pk=evidence['id'])
    e03 = _source_snapshot(e03_attachment)
    with e03_attachment.attachment.open('rb') as stored:
        e03['content'] = stored.read(8192).decode('utf-8')
    e03.update(label='P08-TEMP-E03', prepared=True)
    return {
        'work_order_id': work.pk,
        'machine_id': work.machine_id,
        'repair_packet_id': packet.pk,
        'source_event_id': source.pk,
        'component_code': component.code,
        'e03': e03,
        'as_of': e03_attachment.upload_date,
    }


def search_related_work_manuals(
    work_order_id, actor, query, attachment_id=None, limit=3
):
    """Return bounded literal attached-PDF passages after current owner authorization."""
    from ai.core.integrations.local_controlled_documents import _page_hits

    if (
        not isinstance(query, str)
        or not query.strip()
        or len(query) > 1000
        or type(limit) is not int
        or not 1 <= limit <= 5
        or (
            attachment_id is not None
            and (type(attachment_id) is not int or attachment_id < 1)
        )
    ):
        raise RepairScopeDraftError('Use a bounded query and an actual attachment ID.')
    context = related_work_manual_context(work_order_id, actor)
    candidates = context['manuals']
    if attachment_id is not None:
        candidates = [row for row in candidates if row['id'] == attachment_id]
    terms = tuple(dict.fromkeys(re.findall(r'[a-z0-9][a-z0-9.]{2,}', query.lower())))[
        :30
    ]
    results = []
    for candidate in candidates[:20]:
        attachment = Attachment.objects.get(pk=candidate['id'])
        with attachment.attachment.open('rb') as source:
            content = source.read(_MAX_SOURCE_BYTES + 1)
        if (
            len(content) > _MAX_SOURCE_BYTES
            or hashlib.sha256(content).hexdigest() != candidate['sha256']
        ):
            continue
        for score, page, excerpt in _page_hits(content, terms):
            results.append({
                **candidate,
                'page': page,
                'excerpt': excerpt,
                'source_url': attachment.attachment.url + f'#page={page}',
                'score': score,
                'as_of': attachment.upload_date,
            })
    results.sort(key=lambda row: (-row['score'], row['id'], row['page']))
    return {**context, 'results': results[:limit]}


def repair_scope_draft_context(work_order_id, actor):
    """Resolve real case/source IDs for a proposal without granting execution."""
    current = read_repair_scope_draft(work_order_id, actor)
    work = WorkOrder.objects.select_related('machine').get(pk=current['work_order_id'])
    manuals = related_work_manual_context(work_order_id, actor)
    inspection = read_prepared_work_inspection(work_order_id, actor)
    return {
        **current,
        'title': work.title,
        'machine_name': work.machine.name,
        'source_event_id': manuals['source_event_id'],
        'component_code': manuals['component_code'],
        'sources': {'e03': inspection['e03'], 'manuals': manuals['manuals']},
        'gates': dict(_GATES),
        'manual_applicability': 'Unverified related reference; confirm the fitted cooler before applying instructions.',
    }


@transaction.atomic
def save_repair_scope_draft(
    *,
    work_order_id,
    actor,
    expected_version,
    idempotency_key,
    machine_id,
    repair_packet_id,
    source_event_id,
    component_code,
    e03_attachment_id,
    manual_attachment_ids,
    scope,
    expected_source_hashes=None,
):
    """Persist one proposed scope without approving safety, parts or execution."""
    actor = _actor(actor, change=True)
    work = _locked_work_order(_integer(work_order_id, 'work_order_id'))
    _require_source_scope(actor, work)
    expected_version = _integer(expected_version, 'expected_version')
    idempotency_key = _text(idempotency_key, 'idempotency_key', 128)
    machine_id = _integer(machine_id, 'machine_id')
    repair_packet_id = _integer(repair_packet_id, 'repair_packet_id')
    source_event_id = _integer(source_event_id, 'source_event_id')
    e03_attachment_id = _integer(e03_attachment_id, 'e03_attachment_id')
    component_code = _text(component_code, 'component_code', 100)
    scope = _scope(scope)
    if (
        not isinstance(manual_attachment_ids, list)
        or not 1 <= len(manual_attachment_ids) <= 5
    ):
        raise RepairScopeDraftError('Supply one to five manual attachment IDs.')
    manual_attachment_ids = sorted({
        _integer(value, 'manual_attachment_ids') for value in manual_attachment_ids
    })
    payload = {
        'expected_version': expected_version,
        'machine_id': machine_id,
        'repair_packet_id': repair_packet_id,
        'source_event_id': source_event_id,
        'component_code': component_code,
        'e03_attachment_id': e03_attachment_id,
        'manual_attachment_ids': manual_attachment_ids,
        'scope': scope,
    }
    digest = _canonical_hash(COMMAND, actor, payload)
    packet = (
        RepairPacket.objects
        .select_for_update()
        .filter(pk=repair_packet_id, work_order=work, machine_id=machine_id)
        .first()
    )
    if packet is None or work.machine_id != machine_id:
        raise RepairScopeDraftError('The work order, packet and machine do not match.')
    source = exercise_source(work)
    if source is None or source.pk != source_event_id:
        raise RepairScopeDraftError(
            'The work order does not own this prepared source event.'
        )
    source = MachineAnomaly.objects.select_for_update().get(pk=source.pk)
    if (
        source.machine_id != work.machine_id
        or source.work_order_id != work.pk
        or source.repair_packet_id != packet.pk
    ):
        raise RepairScopeDraftError('The source event ownership changed.')
    binding = (
        source.bindings
        .select_related('dictionary_point__component')
        .filter(pk=source.metrics.get('binding_id'))
        .first()
    )
    component = binding.dictionary_point.component if binding else None
    if component is None or component.code != component_code:
        raise RepairScopeDraftError(
            'The source event does not match the motor component.'
        )
    evidence = _evidence_registry(work).get('P08-TEMP-E03')
    if evidence is None or evidence['id'] != e03_attachment_id:
        raise RepairScopeDraftError(
            'The original prepared E03 evidence is unavailable.'
        )
    e03 = _source_snapshot(Attachment.objects.get(pk=e03_attachment_id))
    e03.update(label='P08-TEMP-E03', prepared=True)
    allowed_sources = (
        Q(model_type='assetmachine', model_id=machine_id)
        | Q(model_type='assetcomponent', model_id=component.pk)
        | Q(model_type='repairpacket', model_id=packet.pk)
        | Q(model_type='kanbancard', model_id=work.pk)
    )
    manuals = list(
        Attachment.objects.filter(
            allowed_sources, pk__in=manual_attachment_ids
        ).order_by('pk')
    )
    if len(manuals) != len(manual_attachment_ids):
        raise RepairScopeDraftError(
            'A manual is not attached to this equipment or job.'
        )
    manual_sources = []
    for manual in manuals:
        snapshot = _source_snapshot(manual)
        if PurePosixPath(snapshot['filename']).suffix.lower() != '.pdf':
            raise RepairScopeDraftError(
                'Manual sources must be attached PDF documents.'
            )
        snapshot['applicability'] = 'related_reference_unverified'
        manual_sources.append(snapshot)
    current_hashes = {
        'e03': {'id': e03['id'], 'sha256': e03['sha256']},
        'manuals': [
            {'id': row['id'], 'sha256': row['sha256']} for row in manual_sources
        ],
    }
    if expected_source_hashes is not None and expected_source_hashes != current_hashes:
        raise RepairScopeDraftError(
            'The reviewed source bytes changed; review them again.'
        )
    replay = _replay_or_none(work, idempotency_key, digest)
    if replay is not None:
        event = WorkOrderEvent.objects.get(pk=replay.event_id, work_order=work)
        if event.metadata['result_metadata']['draft']['sources'] != {
            'e03': e03,
            'manuals': manual_sources,
        }:
            raise RepairScopeDraftError(
                'The saved draft sources changed; review them again.'
            )
        return _snapshot(event)
    _require_version(work, expected_version)
    if (
        not work.is_active
        or work.lifecycle_status
        in {WorkOrderLifecycle.COMPLETED, WorkOrderLifecycle.CANCELED}
        or packet.status not in {PacketStatus.DRAFT, PacketStatus.DIAGNOSED}
        or source.status not in {'open', 'acknowledged'}
        or source.severity != 'critical'
    ):
        raise RepairScopeDraftError(
            'A proposed scope requires an active critical case.'
        )
    previous = work.events.filter(event_type=EVENT_TYPE).order_by('-pk').first()
    revision = (
        previous.metadata['result_metadata']['draft']['revision'] + 1 if previous else 1
    )
    draft = {
        'revision': revision,
        'work_order_id': work.pk,
        'machine_id': machine_id,
        'repair_packet_id': packet.pk,
        'source_event_id': source.pk,
        'component_code': component.code,
        'status': 'proposed',
        'gates': dict(_GATES),
        'scope': scope,
        'sources': {'e03': e03, 'manuals': manual_sources},
    }
    work.lifecycle_version += 1
    work.save(update_fields=['lifecycle_version'])
    result = _append_result(
        work_order=work,
        actor=actor,
        command=COMMAND,
        event_type=EVENT_TYPE,
        from_status=work.lifecycle_status,
        to_status=work.lifecycle_status,
        reason='Source-linked repair scope proposed; review and physical work remain pending.',
        correlation_id=uuid.uuid4(),
        idempotency_key=idempotency_key,
        request_hash=digest,
        result_metadata={'draft': draft},
    )
    return _snapshot(WorkOrderEvent.objects.get(pk=result.event_id))
