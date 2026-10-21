"""P20 / UI12: the saved investigation behind a work order, as Diagnose shows it.

``investigation.v1`` projects the owning repair packet's saved diagnosis,
evidence citations, alternatives, proposed checks, technician amendments,
findings and the approved intervention basis. It never carries the model's
confidence number: certainty is expressed through the cited evidence, its
relation to the cause and the named gaps.

P41 adds canonical Work-owned request/evidence records. P42 retains earlier
finding observations as attributed revisions; historic values overwritten before
that feature cannot be reconstructed.
"""

from __future__ import annotations

from tasks.services.investigation_requests import project_requests

from .common import packet_of

RULE = 'investigation.v1'


def _text(item) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        for key in ('text', 'cause', 'observation', 'note', 'description', 'name'):
            value = item.get(key)
            if isinstance(value, str) and value:
                return value
    return ''


def _amendment(item) -> dict | None:
    text = _text(item)
    if not text:
        return None
    meta = item if isinstance(item, dict) else {}
    return {
        'text': text,
        'author': meta.get('author') or meta.get('by') or meta.get('user') or None,
        'recorded_at': meta.get('at') or meta.get('created_at') or None,
        'reason': meta.get('reason') or None,
    }


def _evidence(item) -> dict:
    return {
        'observation': item.get('observation') or '',
        'relation': item.get('relation') or 'unknown',
        'snapshot_id': item.get('snapshot_id'),
        'signal_label': item.get('signal_label'),
        'value': item.get('value'),
        'unit': item.get('unit'),
        'observed_at': item.get('observed_at'),
        'stale': item.get('stale'),
    }


def _explanation(packet) -> dict | None:
    from repair.schema import coerce_diagnosis, is_preliminary

    raw = packet.diagnosis if isinstance(packet.diagnosis, dict) else {}
    if not raw or packet.diagnosis_schema_version == 0:
        return None
    diagnosis = coerce_diagnosis(raw)  # Normalize prose/citations, not provenance.
    window = raw.get('data_window') or {}
    freshness = raw.get('freshness') or {}
    quality = raw.get('quality') or {}
    has_window = bool(window.get('start') or window.get('end'))
    health_rule = raw.get('provider') == 'machine_health.preliminary'
    status = raw.get('status')
    # The keyword heuristic and schema coercion infer 'available' from prose.
    if raw.get('generator') == 'heuristic' or (
        status == 'available' and not raw.get('evidence') and not health_rule
    ):
        status = None
    authority = (
        raw.get('authority') if (raw.get('authority_source') or health_rule) else None
    )
    return {
        'likely_cause': diagnosis.get('likely_cause') or '',
        'failure_mode': diagnosis.get('failure_mode') or None,
        'preliminary': is_preliminary(raw),
        'verified_by': diagnosis.get('verified_by') or None,
        'verified_at': diagnosis.get('verified_at') or None,
        'analysis_status': status,
        'authority': authority,
        'provider': diagnosis.get('provider') or None,
        'model_or_rule_version': diagnosis.get('model_or_rule_version') or None,
        'generated_at': diagnosis.get('generated_at') or None,
        'data_window': {
            'start': window.get('start'),
            'end': window.get('end'),
            'snapshot_count': window.get('snapshot_count') if has_window else None,
        },
        'stale_signal_count': freshness.get('stale_signal_count')
        if has_window or health_rule
        else None,
        'quality_summary': quality.get('summary')
        if quality.get('summary') not in (None, '', 'unknown')
        else None,
        'evidence': [
            _evidence(item)
            for item in diagnosis.get('evidence') or []
            if isinstance(item, dict)
        ],
        'alternatives': [
            text for text in map(_text, diagnosis.get('alternatives') or []) if text
        ],
        'proposed_checks': [
            text for text in map(_text, diagnosis.get('confirm_tests') or []) if text
        ],
        'amendments': [
            amendment
            for amendment in map(_amendment, diagnosis.get('amendments') or [])
            if amendment
        ],
    }


def _findings(packet) -> list[dict]:
    from .common import person

    return [
        {
            'id': finding.pk,
            'finding_key': finding.finding_key,
            'category': finding.category,
            'observation': finding.observation,
            'value': finding.value,
            'unit': finding.unit,
            'evidence_source': finding.evidence_source,
            'snapshot_id': str(finding.snapshot_id) if finding.snapshot_id else None,
            'observed_at': finding.observed_at.isoformat()
            if finding.observed_at
            else None,
            'verification': finding.verification,
            'recorded_by': person(finding.recorded_by),
            'created_at': finding.created_at.isoformat(),
            'updated_at': finding.updated_at.isoformat(),
        }
        for finding in packet.findings.select_related('recorded_by').order_by(
            'sequence', 'pk'
        )
    ]


def _intervention(packet) -> dict | None:
    from .common import person

    scopes = list(
        packet.approved_scopes.select_related('approved_by').order_by('-version')
    )
    candidates = [scope for scope in scopes if scope.superseded_at is None]
    if len(candidates) != 1 or candidates[0].pk != scopes[0].pk:
        return None
    current = candidates[0]
    return {
        'scope_id': current.pk,
        'version': current.version,
        'verified_cause': current.verified_cause,
        'failure_codes': list(current.failure_codes or []),
        'scope_line_count': len(current.scope_lines or []),
        'approved_by': person(current.approved_by),
        'approved_at': current.approved_at.isoformat(),
        'superseded_versions': len(scopes) - 1,
    }


def project(work_order) -> dict:
    """Return the ``investigation.v1`` projection for one work order."""
    packet = packet_of(work_order)
    if packet is None:
        return {
            'rule': RULE,
            'packet': None,
            'explanation': None,
            'findings': [],
            'finding_history': 'not_applicable',
            'intervention': None,
            'requests': project_requests(work_order),
        }
    return {
        'rule': RULE,
        'packet': {
            'id': packet.pk,
            'reference': packet.reference,
            'generation_status': packet.generation_status,
        },
        'explanation': _explanation(packet),
        'findings': _findings(packet),
        # Current ids stay stable; earlier observations are separate revisions.
        'finding_history': 'preserved_revisions',
        'finding_revisions': _revisions(packet),
        'intervention': _intervention(packet),
        'requests': project_requests(work_order),
    }


def _revisions(packet):
    from repair.models import RepairFindingRevision

    from .common import person

    return [
        {
            'id': row.pk,
            'finding_id': row.finding_id,
            'snapshot': row.snapshot,
            'replaced_by': person(row.replaced_by),
            'replaced_at': row.replaced_at.isoformat(),
        }
        for row in RepairFindingRevision.objects
        .filter(finding__packet=packet)
        .select_related('replaced_by')
        .order_by('replaced_at', 'pk')
    ]
