"""P18 / UI11: recorded digital work AIMMS did for one work order.

``handled_actions.v1`` lists only actions that left a durable record:

* repair packet preparation runs (``RepairPacketGenerationRun``);
* the work order the packet preparation created (``work_order_created``
  packet event naming this work order);
* maintenance accounts drafted from a technician narrative
  (``CloseoutProposal``) and narratives still being extracted;
* post-closeout follow-ups (``CloseoutEffect``: notification, learning draft).

A completed chat tool call, an agent trace or a proposal nobody executed is
not a saved business result and is not listed. The projection only reads;
opening the Work page never starts preparation. Closeout-owned rows follow
the closeout API's own deployment gate, and the response says which sources
were read so an empty list is not mistaken for "nothing happened".
"""

from __future__ import annotations

from .common import closeout_of, closeout_wizard_enabled, packet_of, person

RULE = 'handled_actions.v1'

# Recorded status -> what the Work page may say about it.
COMPLETED = 'completed'
ACTIVE = 'active'
FAILED = 'failed'
UNKNOWN = 'unknown'

_RUN_STATE = {'running': ACTIVE, 'succeeded': COMPLETED, 'failed': FAILED}

_EFFECT_STATE = {
    'pending': ACTIVE,
    'leased': ACTIVE,
    'dispatching': ACTIVE,
    'retryable': ACTIVE,
    'outcome_unknown': UNKNOWN,
    'succeeded': COMPLETED,
    'failed': FAILED,
    'abandoned': FAILED,
}


def _iso(value):
    return value.isoformat() if value else None


def _generation_actions(packet) -> list[dict]:
    """One row per recorded preparation run of the owning repair packet."""
    rows = []
    for run in packet.generation_runs.all().order_by('-started_at', '-pk'):
        summary = run.result_summary if isinstance(run.result_summary, dict) else {}
        state = _RUN_STATE.get(run.status, UNKNOWN)
        result = None
        if state == COMPLETED:
            # The run's own counts. Confidence and chat turn ids are recorded
            # for provenance but are not a business result.
            result = {
                'parts': summary.get('parts'),
                'gates_created': summary.get('gates_created'),
            }
        rows.append({
            'id': f'packet-run:{run.pk}',
            'kind': 'packet_prepared',
            'state': state,
            'status': run.status,
            'occurred_at': _iso(run.finished_at or run.started_at),
            'started_at': _iso(run.started_at),
            'finished_at': _iso(run.finished_at),
            'source': {
                'record': 'repair_packet_generation_run',
                'id': run.pk,
                'provider': run.provider or None,
            },
            'trigger': None,
            'result': result,
            'link': {
                'kind': 'repair_packet',
                'id': packet.pk,
                'reference': packet.reference,
            },
            'attempts': None,
        })
    return rows


def _work_order_created_actions(packet, work_order) -> list[dict]:
    """The packet preparation that created this very work order."""
    rows = []
    for event in packet.events.filter(event_type='work_order_created').order_by(
        '-created_at', '-pk'
    ):
        metadata = event.metadata if isinstance(event.metadata, dict) else {}
        if metadata.get('work_order_id') != work_order.pk:
            continue
        rows.append({
            'id': f'packet-event:{event.pk}',
            'kind': 'work_order_prepared',
            'state': COMPLETED,
            'status': 'recorded',
            'occurred_at': _iso(event.created_at),
            'started_at': None,
            'finished_at': _iso(event.created_at),
            'source': {
                'record': 'repair_packet_event',
                'id': event.pk,
                'provider': None,
            },
            # The person whose request started the preparation, when the
            # packet event recorded one.
            'trigger': {'requested_by': person(event.actor)},
            'result': {'reference': work_order.reference},
            'link': {'kind': 'work_panel', 'panel': 'parts'},
            'attempts': None,
        })
    return rows


def _closeout_extraction_actions(work_order) -> list[dict]:
    """Maintenance accounts drafted from narratives, and extractions under way."""
    from tasks.closeout_models import CloseoutCapture, CloseoutProposal

    rows = []
    proposals = (
        CloseoutProposal.objects
        .filter(capture_revision__capture__work_order=work_order)
        .select_related('capture_revision')
        .order_by('-created_at', '-pk')
    )
    for proposal in proposals:
        fields = proposal.fields if isinstance(proposal.fields, dict) else {}
        rows.append({
            'id': f'closeout-proposal:{proposal.pk}',
            'kind': 'account_drafted',
            'state': COMPLETED,
            'status': proposal.status,
            'occurred_at': _iso(proposal.created_at),
            'started_at': None,
            'finished_at': _iso(proposal.created_at),
            'source': {
                'record': 'closeout_proposal',
                'id': proposal.pk,
                'provider': proposal.extractor or None,
            },
            'trigger': None,
            'result': {
                'capture_revision': proposal.capture_revision.revision,
                'fields': len(fields),
                'part_candidates': len(proposal.part_candidates or []),
                'reading_candidates': len(proposal.reading_candidates or []),
            },
            'link': {'kind': 'work_panel', 'panel': 'closeout'},
            'attempts': None,
        })
    extracting = CloseoutCapture.objects.filter(
        work_order=work_order, status='extracting'
    ).select_related('current_revision')
    for capture in extracting.order_by('-pk'):
        revision = capture.current_revision
        rows.append({
            'id': f'closeout-capture:{capture.pk}',
            'kind': 'account_drafting',
            'state': ACTIVE,
            'status': capture.status,
            'occurred_at': _iso(revision.created_at if revision else None),
            'started_at': _iso(revision.created_at if revision else None),
            'finished_at': None,
            'source': {
                'record': 'closeout_capture',
                'id': capture.pk,
                'provider': None,
            },
            'trigger': None,
            'result': None,
            'link': {'kind': 'work_panel', 'panel': 'closeout'},
            'attempts': None,
        })
    return rows


def _effect_result(effect) -> dict | None:
    """Translate the recorded effect result reference without guessing."""
    reference = effect.result_reference or ''
    if effect.effect_type == 'notification':
        if reference.startswith('notified:'):
            try:
                return {'notified': int(reference.split(':', 1)[1])}
            except ValueError:
                return None
        if reference == 'no notification targets configured':
            return {'notified': 0, 'no_targets': True}
    if effect.effect_type == 'memory_draft' and reference.startswith('draft:'):
        return {'learning_draft': reference.split(':', 1)[1]}
    if reference.startswith('reconciled:'):
        return {'reconciled': True}
    return None


def _effect_actions(closeout) -> list[dict]:
    rows = []
    for effect in closeout.effects.all().order_by('-created_at', '-pk'):
        state = _EFFECT_STATE.get(effect.status, UNKNOWN)
        rows.append({
            'id': f'closeout-effect:{effect.pk}',
            'kind': (
                'closeout_notification'
                if effect.effect_type == 'notification'
                else 'learning_drafted'
                if effect.effect_type == 'memory_draft'
                else 'closeout_follow_up'
            ),
            'state': state,
            'status': effect.status,
            'occurred_at': _iso(effect.resolved_at or effect.created_at),
            'started_at': _iso(effect.created_at),
            'finished_at': _iso(effect.resolved_at),
            'source': {
                'record': 'closeout_effect',
                'id': effect.pk,
                'provider': effect.effect_type,
            },
            'trigger': {'closeout_completed_at': _iso(closeout.completed_at)},
            'result': _effect_result(effect) if state == COMPLETED else None,
            'link': {'kind': 'work_panel', 'panel': 'closeout'},
            'attempts': effect.attempts,
        })
    return rows


def project(work_order) -> dict:
    """Return the ``handled_actions.v1`` projection for one work order."""
    actions: list[dict] = []
    sources = []

    packet = packet_of(work_order)
    if packet is None:
        sources.append({'key': 'packet_preparation', 'state': 'not_applicable'})
    else:
        actions += _generation_actions(packet)
        actions += _work_order_created_actions(packet, work_order)
        sources.append({'key': 'packet_preparation', 'state': 'read'})

    if not closeout_wizard_enabled():
        sources.append({'key': 'closeout_drafting', 'state': 'not_enabled'})
        sources.append({'key': 'closeout_follow_up', 'state': 'not_enabled'})
    else:
        actions += _closeout_extraction_actions(work_order)
        sources.append({'key': 'closeout_drafting', 'state': 'read'})
        closeout = closeout_of(work_order)
        if closeout is None:
            sources.append({'key': 'closeout_follow_up', 'state': 'not_applicable'})
        else:
            actions += _effect_actions(closeout)
            sources.append({'key': 'closeout_follow_up', 'state': 'read'})

    actions.sort(key=lambda row: row['occurred_at'] or '', reverse=True)
    counts = {COMPLETED: 0, ACTIVE: 0, FAILED: 0, UNKNOWN: 0}
    for row in actions:
        counts[row['state']] += 1
    return {'rule': RULE, 'sources': sources, 'counts': counts, 'actions': actions}
