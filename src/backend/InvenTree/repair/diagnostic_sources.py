"""Literal related manuals and explicitly requested prepared inspection evidence."""

import json

from django.core.exceptions import ObjectDoesNotExist, PermissionDenied

from tasks.services.repair_scope_drafts import (
    read_prepared_work_inspection,
    search_related_work_manuals,
)
from tasks.services.work_orders import WorkOrderCommandError

from repair.models import RepairPacket
from repair.services import (
    _diagnostic_evidence,
    _diagnostic_reauthorize,
    _diagnostic_result,
    diagnostic_rehydrate_actor,
)


def read_source(
    *, actor, authorization, tool_name, repair_packet_id, expected_revision, **arguments
):
    """Reauthorize the exact packet and native source owner before exposing text."""
    actor = diagnostic_rehydrate_actor(getattr(actor, 'pk', None))
    capability = {
        'search_related_work_manuals': 'diagnostics.manuals.read',
        'get_requested_prepared_inspection': 'diagnostics.packet.read',
    }.get(tool_name)
    if (
        actor is None
        or capability is None
        or authorization.capability != capability
        or authorization.entity_type != 'repair_packet'
        or authorization.entity_id != repair_packet_id
        or not _diagnostic_reauthorize(actor, authorization, expected_revision)
    ):
        return _diagnostic_result(reason='The requested Work source is unavailable.')
    try:
        packet = RepairPacket.objects.get(pk=repair_packet_id)
        if (
            packet.work_order_id is None
            or packet.machine_id != authorization.linked_machine_id
        ):
            return _diagnostic_result(
                reason='The requested Work source is unavailable.'
            )
        if tool_name == 'search_related_work_manuals':
            result = search_related_work_manuals(
                packet.work_order_id, actor, **arguments
            )
            rows = []
            for row in result['results']:
                claim = {
                    key: value
                    for key, value in row.items()
                    if key not in {'as_of', 'score'}
                }
                claim.update(
                    work_order_id=packet.work_order_id,
                    repair_packet_id=packet.pk,
                    machine_id=packet.machine_id,
                    source_event_id=result['source_event_id'],
                    source_kind='related_attached_manual',
                    uploaded_date=str(row['as_of']),
                    applicability='related_reference_unverified',
                    limitation='Not an approved or verified fitted-motor manual. Confirm the installed motor and cooler before applying instructions.',
                )
                rows.append(
                    _diagnostic_evidence(
                        source_type='related_attached_manual',
                        source_id=row['id'],
                        revision=row['sha256'],
                        locator=row['source_url'],
                        as_of=authorization.checked_at,
                        claim=json.dumps(claim, sort_keys=True),
                        untrusted=True,
                    )
                )
        else:
            result = read_prepared_work_inspection(packet.work_order_id, actor)
            e03 = result['e03']
            rows = [
                _diagnostic_evidence(
                    source_type='prepared_inspection_record',
                    source_id=e03['id'],
                    revision=e03['sha256'],
                    locator=f'work-order:{packet.work_order_id}:attachment:{e03["id"]}#P08-TEMP-E03',
                    as_of=authorization.checked_at,
                    claim=json.dumps(
                        {
                            **e03,
                            'work_order_id': packet.work_order_id,
                            'uploaded_date': str(result['as_of']),
                            'repair_packet_id': packet.pk,
                            'machine_id': packet.machine_id,
                            'source_event_id': result['source_event_id'],
                            'limitation': 'Prepared exercise observation, not an actual field inspection, live measurement, completed repair or operating permission.',
                        },
                        sort_keys=True,
                    ),
                    untrusted=True,
                )
            ]
    except (
        ObjectDoesNotExist,
        PermissionDenied,
        WorkOrderCommandError,
        OSError,
        ValueError,
    ):
        return _diagnostic_result(reason='The requested Work source is unavailable.')
    if not _diagnostic_reauthorize(actor, authorization, expected_revision):
        return _diagnostic_result(reason='The requested Work source is unavailable.')
    return _diagnostic_result(
        *rows, reason='No matching literal source passage was available.'
    )
