"""P43 retained scope history and selective component/procedure reassessment.

Only explicitly recorded dependency identities are mapped. Unbound facts stay
unknown; this read projection does not grant execution or physical authority.
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
    selective = selective_reassessment(ordered, state, work_order)
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
            {
                'kind': kind,
                'available': any(
                    row['kind'] == kind
                    and row['scope_id'] == selective['current_scope_id']
                    for row in selective['items']
                ),
                'reason': 'recorded_component_procedure_bindings'
                if any(
                    row['kind'] == kind
                    and row['scope_id'] == selective['current_scope_id']
                    for row in selective['items']
                )
                else 'no_revision_dependency_links',
            }
            for kind in DEPENDENCIES
        ],
        'evidence_validity': {'available': False, 'reason': 'no_scope_evidence_links'},
        'request_revisions': _request_revisions(work_order),
        'selective_reassessment': selective,
    }


def selective_reassessment(scopes, state, work_order):
    """Only fresh, native Work evidence can clear an affected binding's review."""
    from django.utils.dateparse import parse_datetime

    from assets.registry_models import AssetComponent

    current = scopes[-1] if state in ('original', 'revised') else None
    component_changes, procedure_changes = defaultdict(list), defaultdict(list)
    binding_changes = defaultdict(list)
    unmapped = False

    def identity(row):
        return (
            row.get('affected_component_ref') or None,
            row.get('procedure_revision_id') or None,
        )

    def groups(scope):
        result = defaultdict(list)
        for line in scope['lines']:
            result[identity(line)].append(line['action'])
        return result

    def bindings(scope):
        result = defaultdict(set)
        for line in scope['lines']:
            for row in line.get('dependencies', []):
                result[row['kind'], row['id']].add(identity(row))
        return result

    last_bindings = {}
    for index, scope in enumerate(scopes):
        if index:
            old, new = groups(scopes[index - 1]), groups(scope)
            changed = {
                key for key in set(old) | set(new) if old.get(key) != new.get(key)
            }
            for component, revision in changed:
                if component:
                    component_changes[component].append(index)
                if revision:
                    procedure_changes[revision].append(index)
                if not component and not revision:
                    unmapped = True
        for key, after in bindings(scope).items():
            previous = last_bindings.get(key)
            if previous:
                previous_index, before = previous
                reappeared = previous_index < index - 1
                changed_bindings = before | after if reappeared else before ^ after
                for component, revision in changed_bindings:
                    binding_changes[(*key, component, revision)].append(index)
                old_components = {component for component, _ in before}
                new_components = {component for component, _ in after}
                old_revisions = {revision for _, revision in before}
                new_revisions = {revision for _, revision in after}
                for component in old_components ^ new_components:
                    if component:
                        component_changes[component].append(index)
                for revision in old_revisions ^ new_revisions:
                    if revision:
                        procedure_changes[revision].append(index)
            last_bindings[key] = (index, after)
    components = set(
        AssetComponent.objects.filter(machine_id=work_order.machine_id).values_list(
            'code', flat=True
        )
    )
    revisions = set(
        work_order.procedure_applications.values_list('revision_id', flat=True)
    )
    artifacts = {
        row.pk: row
        for row in InvestigationArtifact.objects.filter(request__work_order=work_order)
    }
    rows = []
    for scope_index, scope in enumerate(scopes):
        for line_index, line in enumerate(scope['lines']):
            for binding_index, binding in enumerate(line.get('dependencies', [])):
                component, revision = identity(binding)
                key = binding['kind'], binding['id']
                changes = [
                    i
                    for i in component_changes[component]
                    + procedure_changes[revision]
                    + binding_changes[(*key, component, revision)]
                    if i <= scope_index
                ]
                affected = bool(changes)
                evidence = sorted(set(binding.get('evidence_ids', [])))
                valid = bool(evidence) and all(pk in artifacts for pk in evidence)
                bound = (
                    bool(component or revision)
                    and (not component or component in components)
                    and (not revision or revision in revisions)
                )
                last_change = (
                    scopes[max(changes)].get('approved_at') if changes else None
                )
                trigger = (
                    parse_datetime(last_change)
                    if isinstance(last_change, str)
                    else last_change
                )
                reassessed = (
                    affected
                    and valid
                    and trigger is not None
                    and all(artifacts[pk].created_at > trigger for pk in evidence)
                )
                is_current = bool(current and scope['id'] == current['id'])
                status = (
                    'effective_unknown'
                    if current is None
                    else 'obsolete'
                    if not is_current
                    else 'unmapped'
                    if not bound
                    else 'reassess'
                    if affected and not reassessed
                    else 'evidence_unknown'
                    if not valid
                    else 'retained'
                )
                rows.append({
                    **binding,
                    'scope_id': scope['id'],
                    'line_sequence': line.get('sequence', line_index + 1),
                    'binding_index': binding_index,
                    'state': status,
                    'executable': is_current and status == 'retained',
                    'retained_evidence_ids': evidence
                    if valid and (not affected or reassessed)
                    else [],
                    'cited_evidence_ids': evidence,
                })
    return {
        'rule': 'component_procedure_bindings.v1',
        'current_scope_id': current['id'] if current else None,
        'changed_component_refs': sorted(k for k, v in component_changes.items() if v),
        'changed_procedure_revision_ids': sorted(
            k for k, v in procedure_changes.items() if v
        ),
        'unmapped_changes': unmapped,
        'items': rows,
        'scope_execution': [
            {
                'scope_id': scope['id'],
                'executable': bool(current and scope['id'] == current['id']),
            }
            for scope in scopes
        ],
    }
