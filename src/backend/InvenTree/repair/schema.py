"""Versioned schema + validation for the Repair Packet diagnosis blob.

The diagnosis is persisted as JSON on :class:`repair.models.RepairPacket`.
Keeping the contract in one place lets the serializer, services and generators
agree on a single shape and lets us evolve it with an explicit version number
rather than silent drift.

Schema v2 adds the provenance a preliminary result needs to be trusted:

* ``status`` says whether the analysis had usable data at all - ``unavailable``,
  ``stale`` and ``insufficient`` are first-class answers, not empty strings;
* every ``evidence`` item cites the immutable health snapshot it came from and
  declares whether that observation *supports*, *contradicts* or is merely
  *related to* the stated cause;
* ``data_window``, ``freshness`` and ``quality`` describe what the analysis
  actually saw, so a confident-looking cause built on hours-old telemetry reads
  as exactly that;
* ``authority`` says *who decided the machine is out of limits*. A source system
  that declares its own alarm is authoritative about that - the boundaries are
  configured in the hub the data comes from, and it is the system that owns the
  asset. A condition we inferred from a threshold configured here is
  ``derived``. The distinction only affects how this report reads: an
  authoritative alarm still cannot satisfy a safety gate, mark a repair ready or
  count as a verified diagnosis;
* ``provider`` and ``model_or_rule_version`` identify who produced it.

Until a human confirms it, the whole blob is *preliminary*: ``verified_by_user``
and ``verified_at`` are the only thing that turns "preliminary results" into a
"diagnosis". Technician corrections live in ``amendments``, separate from the
generated content, so regeneration can never quietly overwrite them.
"""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError

# Bump when the diagnosis shape changes in a backwards-incompatible way.
DIAGNOSIS_SCHEMA_VERSION = 2

#: Versions this module can still read. Packets generated before the upgrade keep
#: rendering; they are converted on read rather than migrated in bulk.
SUPPORTED_SCHEMA_VERSIONS = (1, 2)

# --- Preliminary-result status vocabulary ---------------------------------- #
STATUS_AVAILABLE = 'available'
STATUS_UNAVAILABLE = 'unavailable'
STATUS_STALE = 'stale'
STATUS_INSUFFICIENT = 'insufficient'

ANALYSIS_STATUSES = (
    STATUS_AVAILABLE,
    STATUS_UNAVAILABLE,
    STATUS_STALE,
    STATUS_INSUFFICIENT,
)

# --- How one observation relates to the stated cause ------------------------ #
RELATION_SUPPORTS = 'supports'
RELATION_CONTRADICTS = 'contradicts'
RELATION_UNKNOWN = 'unknown'

EVIDENCE_RELATIONS = (RELATION_SUPPORTS, RELATION_CONTRADICTS, RELATION_UNKNOWN)

# --- Who decided the condition is abnormal ---------------------------------- #
#: The source system declared the alarm itself, against boundaries configured in
#: that system. Whether the machine is out of limits is its call, not ours.
AUTHORITY_SOURCE_DECLARED = 'source_declared'
#: We inferred the condition from a threshold configured here.
AUTHORITY_DERIVED = 'derived'

ANALYSIS_AUTHORITIES = (AUTHORITY_SOURCE_DECLARED, AUTHORITY_DERIVED)

#: Generator labels whose diagnoses claim AI analysis. An empty ``evidence``
#: list from one of these forces ``status='insufficient'`` and zero
#: confidence: an uncited AI claim is preliminary prose, not a diagnosis.
AI_GENERATORS = ('wf7',)

# key -> accepted python type(s)
_REQUIRED_FIELDS: dict[str, type | tuple[type, ...]] = {
    'likely_cause': str,
    'confidence': (int, float),
    'alternatives': list,
    'evidence': list,
    'confirm_tests': list,
}

_V2_REQUIRED_FIELDS: dict[str, type | tuple[type, ...]] = {
    'status': str,
    'data_window': dict,
    'provider': str,
    'verified_by_user': bool,
    'amendments': list,
}


def empty_diagnosis() -> dict[str, Any]:
    """Return a valid, empty diagnosis blob (pre-generation state)."""
    return {
        'likely_cause': '',
        'confidence': 0.0,
        'confidence_label': 'unknown',
        'alternatives': [],
        'evidence': [],
        'confirm_tests': [],
        'failure_mode': None,
        # v2 provenance
        'status': STATUS_UNAVAILABLE,
        'authority': AUTHORITY_DERIVED,
        'authority_source': None,
        'data_window': {'start': None, 'end': None, 'snapshot_count': 0},
        'freshness': {'stale': False, 'stale_signal_count': 0},
        'quality': {'summary': 'unknown', 'bad_signal_count': 0},
        'provider': '',
        'model_or_rule_version': '',
        'generated_at': None,
        'verified_by_user': False,
        'verified_at': None,
        'verified_by': None,
        'amendments': [],
        'schema_version': DIAGNOSIS_SCHEMA_VERSION,
    }


def confidence_label(value: float) -> str:
    """Map a 0..1 confidence into the qualitative bands used by wf1."""
    if value >= 0.8:
        return 'high'
    if value >= 0.5:
        return 'medium'
    if value > 0.0:
        return 'low'
    return 'unknown'


def coerce_evidence_item(item: Any) -> dict[str, Any]:
    """Normalize one evidence entry into the v2 citation shape.

    A v1 entry carried only prose. It is preserved verbatim as the observation
    with ``relation='unknown'`` and no snapshot: an uncited claim must not be
    silently promoted to supporting evidence just because the schema now has a
    field for it.
    """
    if not isinstance(item, dict):
        return {
            'snapshot_id': None,
            'observation': str(item),
            'relation': RELATION_UNKNOWN,
        }

    relation = item.get('relation')
    if relation not in EVIDENCE_RELATIONS:
        relation = RELATION_UNKNOWN

    snapshot_id = item.get('snapshot_id')

    normalized = {
        'snapshot_id': str(snapshot_id) if snapshot_id else None,
        'observation': str(
            item.get('observation') or item.get('text') or item.get('note') or ''
        ),
        'relation': relation,
    }

    for key in ('signal_label', 'unit', 'value', 'observed_at', 'quality', 'stale'):
        if key in item:
            normalized[key] = item[key]

    return normalized


def coerce_diagnosis(data: dict[str, Any] | None) -> dict[str, Any]:
    """Best-effort normalise an arbitrary diagnosis dict into schema v2.

    Fills missing keys with safe defaults, clamps ``confidence`` into ``0..1``,
    derives ``confidence_label``, normalizes evidence citations and stamps the
    schema version. Never raises - use :func:`validate_diagnosis` when strict
    checking is required.
    """
    result = empty_diagnosis()

    if isinstance(data, dict):
        for key in ('likely_cause', 'failure_mode'):
            if data.get(key) is not None:
                result[key] = data[key]

        for key in ('alternatives', 'confirm_tests', 'amendments'):
            value = data.get(key)
            if isinstance(value, list):
                result[key] = value

        evidence = data.get('evidence')
        if isinstance(evidence, list):
            result['evidence'] = [coerce_evidence_item(item) for item in evidence]

        for key in ('data_window', 'freshness', 'quality'):
            value = data.get(key)
            if isinstance(value, dict):
                result[key] = value

        for key in (
            'provider',
            'model_or_rule_version',
            'generated_at',
            'verified_at',
            'verified_by',
        ):
            if data.get(key) is not None:
                result[key] = data[key]

        status = data.get('status')
        if status in ANALYSIS_STATUSES:
            result['status'] = status
        elif data.get('likely_cause'):
            # A v1 blob with a cause had, by construction, something to say.
            result['status'] = STATUS_AVAILABLE

        # Unrecognised authority falls back to 'derived'. Claiming a source
        # declared something it did not is the expensive direction to be wrong
        # in, so an unreadable value must never promote a blob to authoritative.
        authority = data.get('authority')
        if authority in ANALYSIS_AUTHORITIES:
            result['authority'] = authority
        if data.get('authority_source') is not None:
            result['authority_source'] = str(data['authority_source'])

        result['verified_by_user'] = bool(data.get('verified_by_user', False))

        raw_conf = data.get('confidence', 0.0)
        try:
            conf = float(raw_conf)
        except (TypeError, ValueError):
            conf = 0.0
        result['confidence'] = max(0.0, min(1.0, conf))

        # Preserve any extra provider-specific keys without clobbering schema.
        for key, value in data.items():
            if key not in result:
                result[key] = value

        # An AI-generated diagnosis that cites nothing must not read as an
        # available, confident answer — regardless of what the generator
        # claimed. The prose stays for human review; the trust does not.
        # The keyword heuristic is exempt: it never claims analysis at all
        # and is labelled as an offline fallback by its own generator value.
        if result.get('generator') in AI_GENERATORS and not result['evidence']:
            result['status'] = STATUS_INSUFFICIENT
            result['confidence'] = 0.0

    result['confidence_label'] = confidence_label(result['confidence'])
    result['schema_version'] = DIAGNOSIS_SCHEMA_VERSION
    return result


def merge_regenerated(previous: dict[str, Any] | None, generated: dict[str, Any]):
    """Apply a new generation run without discarding human-entered facts.

    Regeneration replaces the model's own output. It does *not* clear a
    technician's verification or their amendments: those are statements a person
    made about the machine, and a later model run has no standing to retract
    them. The caller records a fresh generation run so the superseded output
    stays auditable.
    """
    merged = coerce_diagnosis(generated)

    if not isinstance(previous, dict):
        return merged

    previous_amendments = previous.get('amendments')
    if isinstance(previous_amendments, list) and previous_amendments:
        merged['amendments'] = previous_amendments

    if previous.get('verified_by_user'):
        merged['verified_by_user'] = True
        merged['verified_at'] = previous.get('verified_at')
        merged['verified_by'] = previous.get('verified_by')

    return merged


def is_preliminary(data: Any) -> bool:
    """Whether this blob must still be presented as preliminary results.

    Anything that is not an explicitly verified blob is preliminary. Failing
    towards "preliminary" is the safe direction: labelling an unverified guess a
    diagnosis is the error that gets a machine worked on for the wrong reason.
    """
    if not isinstance(data, dict):
        return True
    return not bool(data.get('verified_by_user'))


def validate_diagnosis(data: Any) -> None:
    """Strictly validate a diagnosis blob, raising ``ValidationError`` if bad.

    Both supported versions are accepted: v1 blobs written before the upgrade
    stay readable, and are validated against the fields they were required to
    carry rather than against v2's provenance.
    """
    if not isinstance(data, dict):
        raise ValidationError('Diagnosis must be a JSON object.')

    version = data.get('schema_version', 1)
    if version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ValidationError(f'Unsupported diagnosis schema version {version!r}.')

    missing = [k for k in _REQUIRED_FIELDS if k not in data]
    if missing:
        raise ValidationError(f'Diagnosis missing required keys: {sorted(missing)}')

    for key, expected in _REQUIRED_FIELDS.items():
        if not isinstance(data[key], expected):
            type_name = (
                expected.__name__
                if isinstance(expected, type)
                else '/'.join(t.__name__ for t in expected)
            )
            raise ValidationError(f"Diagnosis field '{key}' must be {type_name}.")

    confidence = float(data['confidence'])
    if not 0.0 <= confidence <= 1.0:
        raise ValidationError('Diagnosis confidence must be between 0 and 1.')

    if version < 2:
        return

    missing_v2 = [k for k in _V2_REQUIRED_FIELDS if k not in data]
    if missing_v2:
        raise ValidationError(
            f'Diagnosis v2 missing required keys: {sorted(missing_v2)}'
        )

    if data['status'] not in ANALYSIS_STATUSES:
        raise ValidationError(
            f'Diagnosis status must be one of: {", ".join(ANALYSIS_STATUSES)}.'
        )

    # Checked only when present: v2 blobs written before authority existed are
    # still valid, and coercion reads them as 'derived'.
    if 'authority' in data and data['authority'] not in ANALYSIS_AUTHORITIES:
        raise ValidationError(
            f'Diagnosis authority must be one of: {", ".join(ANALYSIS_AUTHORITIES)}.'
        )

    for index, item in enumerate(data['evidence']):
        if not isinstance(item, dict):
            raise ValidationError(f'Evidence item {index} must be an object.')
        if item.get('relation') not in EVIDENCE_RELATIONS:
            raise ValidationError(
                f'Evidence item {index} relation must be one of: '
                f'{", ".join(EVIDENCE_RELATIONS)}.'
            )
