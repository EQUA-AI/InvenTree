"""Validate source-owner review claims without creating runtime authority."""

from collections import Counter

SCHEMA = 'aimms.source-owner-closeout-input.v2'
MAX_ROWS = 1000
ROOT_FIELDS = {
    'schema',
    'candidate_manifest_sha256',
    'inventory_sha256',
    'historical_sources',
    'field_decisions',
    'writer_inventory',
    'copy_inventory',
    'unresolved',
    'review_references',
}
DISPOSITIONS = {
    'approved_projection',
    'explicit_withholding',
    'proposed_original_scope_disposition',
}
OPERATIONS = {
    'create',
    'bulk',
    'update',
    'relink',
    'delete',
    'rollback',
    'restore',
    'raw',
    'storage',
}


def _object(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        raise ValueError('Exact declared review fields are required')


def _rows(value):
    if type(value) is not list or len(value) > MAX_ROWS:
        raise ValueError('Bounded review lists are required')
    return value


def _text(value, *, nullable=False):
    if value is None and nullable:
        return
    if type(value) is not str or not 1 <= len(value) <= 4096 or not value.strip():
        raise ValueError('A nonempty evidence reference or explicit null is required')


def _texts(value):
    for row in _rows(value):
        _text(row)


def _digest(value):
    if (
        type(value) is not str
        or len(value) != 64
        or any(c not in '0123456789abcdef' for c in value)
    ):
        raise ValueError('Exact SHA-256 input binding is required')


def input_template(*, manifest_sha256, inventory_sha256):
    """Represent missing facts as null/empty, never invented source attestations."""
    _digest(manifest_sha256)
    _digest(inventory_sha256)
    return {
        'schema': SCHEMA,
        'candidate_manifest_sha256': manifest_sha256,
        'inventory_sha256': inventory_sha256,
        'historical_sources': [],
        'field_decisions': [],
        'writer_inventory': {
            'owner_reference': None,
            'source_incarnation_basis': None,
            'paths': [],
            'known_gaps': ['Source-owned writer inventory not supplied'],
            'completeness_evidence_reference': None,
        },
        'copy_inventory': {
            'owner_reference': None,
            'instances': [],
            'backup_restore_policy_reference': None,
            'unknowns': ['Actual copy and backup inventory not supplied'],
            'completeness_evidence_reference': None,
        },
        'unresolved': ['Authoritative history and source-owner decisions not supplied'],
        'review_references': [],
    }


def validate_inputs(value, *, inventory, manifest_sha256, inventory_sha256):
    """Check candidate-bound claims and enumerate all missing original obligations.

    Referenced evidence is not fetched, executed, approved or verified here.
    Even structurally complete claims confer no grant, historical proof,
    source-field policy, erasure authority or acceptance disposition.
    """
    _object(value, ROOT_FIELDS)
    _digest(manifest_sha256)
    _digest(inventory_sha256)
    if (
        value['schema'] != SCHEMA
        or value['candidate_manifest_sha256'] != manifest_sha256
        or value['inventory_sha256'] != inventory_sha256
    ):
        raise ValueError('Unknown schema or stale source/inventory binding')
    if (
        type(inventory) is not dict
        or inventory.get('schema') != 'aimms.native-contract-audit.v1'
    ):
        raise ValueError('An actual native contract inventory is required')
    expected = set()
    models = set()
    for domain in _rows(inventory.get('domains')):
        model = domain['model']
        _text(model)
        if model in models:
            raise ValueError('Duplicate inventory model')
        models.add(model)
        fields = _rows(domain['fields'])
        seen = set()
        gaps = []
        for entry in fields:
            if type(entry) is not dict:
                raise ValueError('Actual native field metadata is required')
            name = entry['name']
            _text(name)
            if name in seen:
                raise ValueError('Duplicate inventory field')
            seen.add(name)
            if entry['state'] == 'no_reviewed_projection':
                gaps.append(name)
        if domain['policy_gaps'] != gaps:
            raise ValueError(
                'Policy gaps must agree with actual native field classifications'
            )
        for name in gaps:
            expected.add((model, name))
    decisions = {}
    for row in _rows(value['field_decisions']):
        _object(
            row,
            {
                'model',
                'field_or_edge',
                'disposition',
                'native_authority',
                'independent_dependent_authority',
                'semantics_reference',
                'decision_reference',
                'original_clause_impact',
            },
        )
        for text in row.values():
            _text(text)
        key = row['model'], row['field_or_edge']
        if (
            key not in expected
            or key in decisions
            or row['disposition'] not in DISPOSITIONS
        ):
            raise ValueError('Unknown, duplicate or invalid field decision claim')
        decisions[key] = row['disposition']
    histories = set()
    for row in _rows(value['historical_sources']):
        _object(
            row,
            {
                'domain',
                'specification_reference',
                'incarnation_revision_basis',
                'ownership_effective_time_basis',
                'conflict_restore_relink_policy',
                'qualification_reference',
                'limitations',
            },
        )
        for key, item in row.items():
            _texts(item) if key == 'limitations' else _text(item)
        if (
            row['domain'] not in {'work', 'maintenance', 'attachment'}
            or row['domain'] in histories
        ):
            raise ValueError('Unknown or duplicate historical source domain')
        histories.add(row['domain'])
    writers = value['writer_inventory']
    _object(
        writers,
        {
            'owner_reference',
            'source_incarnation_basis',
            'paths',
            'known_gaps',
            'completeness_evidence_reference',
        },
    )
    for key in (
        'owner_reference',
        'source_incarnation_basis',
        'completeness_evidence_reference',
    ):
        _text(writers[key], nullable=True)
    _texts(writers['known_gaps'])
    writer_keys = set()
    for row in _rows(writers['paths']):
        _object(
            row,
            {
                'domain',
                'writer_reference',
                'operations',
                'capture_reference',
                'qualification_reference',
            },
        )
        for key in ('domain', 'writer_reference'):
            _text(row[key])
        for key in ('capture_reference', 'qualification_reference'):
            _text(row[key], nullable=True)
        operations = _rows(row['operations'])
        _texts(operations)
        if (
            not operations
            or len(operations) != len(set(operations))
            or not set(operations) <= OPERATIONS
        ):
            raise ValueError('Explicit supported writer operations are required')
        key = row['domain'], row['writer_reference']
        if key in writer_keys:
            raise ValueError('Duplicate writer path')
        writer_keys.add(key)
    copies = value['copy_inventory']
    _object(
        copies,
        {
            'owner_reference',
            'instances',
            'backup_restore_policy_reference',
            'unknowns',
            'completeness_evidence_reference',
        },
    )
    for key in (
        'owner_reference',
        'backup_restore_policy_reference',
        'completeness_evidence_reference',
    ):
        _text(copies[key], nullable=True)
    _texts(copies['unknowns'])
    copy_keys = set()
    for row in _rows(copies['instances']):
        _object(
            row,
            {
                'class',
                'instance_reference',
                'source_revision_binding',
                'owner_authority_reference',
                'holds_retention_reference',
                'readback_reference',
            },
        )
        for key, item in row.items():
            _text(item, nullable=key == 'readback_reference')
        key = row['class'], row['instance_reference']
        if key in copy_keys:
            raise ValueError('Duplicate copy instance')
        copy_keys.add(key)
    _texts(value['unresolved'])
    _texts(value['review_references'])
    return {
        'schema': 'aimms.closeout-input-check.v1',
        'kind': 'structural_review_claim_check_only',
        'candidate_manifest_sha256': manifest_sha256,
        'inventory_sha256': inventory_sha256,
        'field_decisions_required': len(expected),
        'field_decisions_described': len(decisions),
        'missing_field_decisions': [
            {'model': model, 'field_or_edge': name}
            for model, name in sorted(expected - decisions.keys())
        ],
        'claimed_dispositions': dict(sorted(Counter(decisions.values()).items())),
        'historical_domains_described': sorted(histories),
        'missing_historical_domains': sorted(
            {'work', 'maintenance', 'attachment'} - histories
        ),
        'writer_paths_described': len(writer_keys),
        'writer_paths_without_capture_or_qualification': sum(
            row['capture_reference'] is None or row['qualification_reference'] is None
            for row in writers['paths']
        ),
        'declared_writer_gaps': len(writers['known_gaps']),
        'copy_instances_described': len(copy_keys),
        'copy_instances_without_readback': sum(
            row['readback_reference'] is None for row in copies['instances']
        ),
        'declared_copy_unknowns': len(copies['unknowns']),
        'backup_policy_described': copies['backup_restore_policy_reference']
        is not None,
        'evidence_references_verified': False,
        'source_facts_verified': False,
        'writer_and_copy_inventory_complete': False,
        'confers_runtime_authority': False,
        'changes_field_policy_or_original_scope': False,
        'erasure_authorized': False,
        'original_acceptance_complete': False,
        'limits': 'Valid syntax and described coverage are claims for source-owner/independent review. No reference was fetched or executed; missing rows, history, capture/readback and original oracles remain open. Field disposition claims do not amend runtime disclosure policy or original acceptance.',
    }
