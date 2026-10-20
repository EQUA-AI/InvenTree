"""P43: retained revision facts, without inventing a readiness dependency graph.

Scope lines have ordinal positions, not procedure-step/material/permission ids.
Request audit events preserve before/after snapshots but do not identify an
approved scope or a reason for changing it. Neither chronology nor matching
prose proves those missing relationships.
"""

from collections import defaultdict
from itertools import pairwise

from tasks.investigation_models import InvestigationArtifact

from .common import person

DEPENDENCIES = ('tasks', 'materials', 'resources', 'permissions', 'criteria')
REQUEST_FIELDS = (
    'version',
    'purpose',
    'next_check',
    'status',
    'owner_name',
    'next_checkpoint',
    'required_artifacts',
    'blocked_reason',
    'source_finding',
    'source_proposal',
    'resolution',
    'priority',
)


def _request_revisions(work_order):
    allowed = set(work_order.investigation_requests.values_list('pk', flat=True))
    revisions = []
    artifact_ids = None
    for event in (
        work_order.events
        .filter(event_type='investigation_revise')
        .select_related('actor')
        .order_by('-created_at', '-pk')
    ):
        metadata = event.metadata if isinstance(event.metadata, dict) else {}
        # _append_result stores command result facts under result_metadata.
        result = metadata.get('result_metadata') or {}
        if not isinstance(result, dict):
            continue
        compact = result.get('request_revision')
        if isinstance(compact, dict):
            before, after = compact.get('before'), compact.get('after')
        else:
            before, after = result.get('before'), result.get('request')
        if not isinstance(before, dict) or not isinstance(after, dict):
            continue
        request_id = after.get('id')
        if (
            type(request_id) is not int
            or request_id not in allowed
            or before.get('id') != request_id
            or any(row.get('work_order') != work_order.pk for row in (before, after))
        ):
            continue
        if isinstance(compact, dict):
            if artifact_ids is None:
                artifact_ids = defaultdict(list)
                for request_pk, artifact_pk in (
                    InvestigationArtifact.objects
                    .filter(request_id__in=allowed)
                    .order_by('pk')
                    .values_list('request_id', 'pk')
                ):
                    artifact_ids[request_pk].append(artifact_pk)
            through = compact.get('artifact_through_id')
            if through is not None and (type(through) is not int or through < 1):
                continue
            retained = [
                pk
                for pk in artifact_ids[request_id]
                if type(through) is int and pk <= through
            ]
        else:
            retained = [
                item['id']
                for item in before.get('artifacts', [])
                if isinstance(item, dict) and type(item.get('id')) is int
            ]
        revisions.append({
            'event_id': event.pk,
            'request_id': request_id,
            'before': {key: before.get(key) for key in REQUEST_FIELDS},
            'after': {key: after.get(key) for key in REQUEST_FIELDS},
            'retained_evidence_ids': retained,
            'recorded_by': person(event.actor),
            'recorded_at': event.created_at.isoformat(),
            'scope_link': None,
            'revision_reason': None,
        })
    return revisions


def project(work_order, scopes):
    """Project native versions; unavailable impact never means unaffected."""
    ordered = sorted(scopes, key=lambda item: (item['version'], item['id']))
    current = [item for item in ordered if item['current']]
    if not ordered:
        state = 'no_scope'
    elif not current:
        state = 'no_current_scope'
    elif len(current) != 1 or current[0]['id'] != ordered[-1]['id']:
        state = 'conflicting_scope'
    else:
        state = 'revised' if len(ordered) > 1 else 'original'
    return {
        'rule': 'scope_revision.v1',
        'state': state,
        'current_scope_id': current[0]['id']
        if state in ('original', 'revised')
        else None,
        'comparisons': [
            {
                'before_scope_id': before['id'],
                'after_scope_id': after['id'],
                'basis': 'retained_version_order',
            }
            for before, after in reversed(list(pairwise(ordered)))
        ],
        'dependency_impact': [
            {'kind': kind, 'available': False, 'reason': 'no_revision_dependency_links'}
            for kind in DEPENDENCIES
        ],
        'evidence_validity': {'available': False, 'reason': 'no_scope_evidence_links'},
        'request_revisions': _request_revisions(work_order),
    }
