"""Specific incomplete readiness findings; a code catalog never proves all copies."""

from integration_state.s7_contracts import SourceAspectIdentity


def scope_coverage(scope, *, placement_capture_verified):
    """Describe known capture limits without inventing source/store completeness."""
    if (
        type(scope) is not tuple
        or not 1 <= len(scope) <= 1000
        or type(placement_capture_verified) is not bool
    ):
        raise ValueError(
            'A bounded declared scope and exact capture result are required'
        )
    domains = set()
    for item in scope:
        if type(item) is not SourceAspectIdentity:
            raise ValueError('Exact source coordinates are required')
        SourceAspectIdentity.__post_init__(item)
        domains.add((item.entity_kind, item.aspect))
    rows = []
    for kind, aspect in sorted(domains):
        placement = kind == 'machine' and aspect == 'placement'
        future_only = kind in ('work', 'maintenance', 'attachment')
        rows.append({
            'entity_kind': kind,
            'subject_presence_verified': False,
            'subject_history_verified': False,
            'writer_inventory_complete': False,
            'evidence_basis': 'verified_capture_catalog_only'
            if placement and placement_capture_verified
            else 'source_code_capability_only'
            if future_only
            else 'no_qualified_capture',
            'aspect': aspect,
            'capture_state': 'transactional_placement_verified'
            if placement and placement_capture_verified
            else 'placement_capture_unavailable'
            if placement
            else 'future_insert_markers_only'
            if future_only
            else 'unqualified',
            'known_writer_paths': [
                'database_insert',
                'database_update',
                'database_delete',
            ]
            if placement and placement_capture_verified
            else ['native_single_record_insert']
            if future_only
            else [],
            'unqualified_writer_paths': [
                'complete_writer_inventory',
                'historical_baseline',
                'restore_and_external_writers',
                *(
                    [
                        'bulk_insert',
                        'bulk_update',
                        'direct_database_write',
                        'relink_and_removal',
                        'original_storage_replacement',
                    ]
                    if future_only
                    else []
                ),
            ],
            'historical_attribution': 'unproven',
            'copy_instance_inventory': 'unqualified',
            'backup_restore_retention': 'unqualified',
            'coverage_complete': False,
        })
    return {
        'schema': 'aimms.scope-coverage.v1',
        'scope_is_requester_declared': True,
        'domains': rows,
        'coverage_complete': False,
    }
