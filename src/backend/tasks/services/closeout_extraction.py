"""Closeout extraction contract: schema v1 validation and the extractor seam.

The narrative is adversarial input. This module is the Django-side authority
that decides whether an extractor's output is acceptable: schema-only (extra
keys rejected), every populated value anchored to a source span, candidates as
text with no identity resolution, and unknown schema versions failing closed.
Validation never executes anything from the output; hostile content can only
ever become inert, span-anchored strings inside an untrusted proposal row.
"""

import hashlib
import json
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.utils.module_loading import import_string

from tasks.services.work_orders import WorkOrderCommandError

EXTRACTION_SCHEMA_VERSION = 1

# The only field paths extraction may propose (matches WorkOrderCloseout).
EXTRACTION_FIELDS = (
    'cause',
    'action',
    'result',
    'verification_summary',
    'downtime_minutes',
    'follow_up',
)

_TOP_LEVEL_KEYS = {
    'schema_version',
    'fields',
    'part_candidates',
    'reading_candidates',
    'warnings',
}
_FIELD_KEYS = {'value', 'spans', 'confidence', 'warnings'}
_PART_CANDIDATE_KEYS = {'text', 'spans', 'quantity_text', 'warnings'}
_READING_CANDIDATE_KEYS = {'text', 'spans', 'value_text', 'unit_text', 'warnings'}

# Identity resolution is forbidden by contract (FR-CO-003); any of these keys
# appearing anywhere in extractor output rejects the whole run.
_FORBIDDEN_ID_KEYS = {
    'id',
    'pk',
    'ids',
    'part_id',
    'part_pk',
    'stock_item_id',
    'stock_id',
    'user_id',
    'approval_id',
    'allocation_id',
}

_MAX_WARNING_LENGTH = 64
_MAX_WARNINGS = 32
_MAX_CANDIDATES = 64
_MAX_VALUE_LENGTH = 4000


class ExtractionUnavailable(WorkOrderCommandError):  # noqa: N818 - established command error name
    """Extraction is disabled, unconfigured, or the extractor call failed."""

    code = 'EXTRACTION_UNAVAILABLE'


class ExtractionSchemaUnknown(WorkOrderCommandError):  # noqa: N818 - established command error name
    """The extractor returned an unknown schema version; fail closed."""

    code = 'EXTRACTION_SCHEMA_UNKNOWN'


class ExtractionInvalidOutput(WorkOrderCommandError):  # noqa: N818 - established command error name
    """The extractor output violated the schema-only contract."""

    code = 'EXTRACTION_INVALID_OUTPUT'


def extraction_enabled() -> bool:
    """Whether the deployment allows AI extraction at all."""
    return bool(getattr(settings, 'AIMMS_CLOSEOUT_EXTRACTION_ENABLED', False))


def resolve_extractor():
    """Return the configured extractor callable, failing closed when absent.

    ``AIMMS_CLOSEOUT_EXTRACTOR`` is a callable or dotted path taking
    ``(narrative, shape)`` and returning the schema-v1 output document. The
    default deployment adapter is the tool-free capability in
    ``ai.core.workflows.closeout_extraction``.
    """
    if not extraction_enabled():
        raise ExtractionUnavailable('Closeout extraction is disabled')
    extractor = getattr(settings, 'AIMMS_CLOSEOUT_EXTRACTOR', None)
    if isinstance(extractor, str):
        try:
            extractor = import_string(extractor)
        except ImportError as exc:
            raise ExtractionUnavailable(
                'Configured closeout extractor cannot be imported'
            ) from exc
    if not callable(extractor):
        raise ExtractionUnavailable('No closeout extractor is configured')
    return extractor


def content_hash(document) -> str:
    """Canonical hash of a validated extraction document."""
    return hashlib.sha256(
        json.dumps(
            document, sort_keys=True, separators=(',', ':'), default=str
        ).encode()
    ).hexdigest()


def _reject(message: str):
    raise ExtractionInvalidOutput(message)


def _require_no_identity_keys(mapping, where: str):
    for key in mapping:
        if str(key).lower() in _FORBIDDEN_ID_KEYS:
            _reject(f'Identity resolution is forbidden: {where} contains {key!r}')


def _validate_spans(spans, narrative: str, where: str, *, required: bool):
    if not isinstance(spans, list):
        _reject(f'{where}: spans must be a list')
    if required and not spans:
        _reject(f'{where}: a populated value requires at least one source span')
    for span in spans:
        if (
            not isinstance(span, (list, tuple))
            or len(span) != 2
            or not all(isinstance(edge, int) for edge in span)
        ):
            _reject(f'{where}: each span must be a [start, end] integer pair')
        start, end = span
        if start < 0 or end > len(narrative) or start >= end:
            _reject(f'{where}: span [{start}, {end}] is not anchored in the narrative')
    return [list(span) for span in spans]


def _validate_warnings(warnings, where: str):
    if not isinstance(warnings, list) or len(warnings) > _MAX_WARNINGS:
        _reject(f'{where}: warnings must be a bounded list')
    for warning in warnings:
        if not isinstance(warning, str) or len(warning) > _MAX_WARNING_LENGTH:
            _reject(f'{where}: warnings must be short strings')
    return list(warnings)


def _validate_field(name: str, payload, narrative: str):
    if not isinstance(payload, dict):
        _reject(f'field {name!r} must be an object')
    _require_no_identity_keys(payload, f'field {name!r}')
    extra = set(payload) - _FIELD_KEYS
    if extra:
        _reject(f'field {name!r} has unknown keys: {sorted(extra)}')
    if 'value' not in payload or 'spans' not in payload:
        _reject(f'field {name!r} must contain value and spans')

    value = payload['value']
    if name == 'downtime_minutes':
        if value is not None and (not isinstance(value, int) or value < 0):
            _reject('downtime_minutes must be a non-negative integer or null')
        populated = value is not None
    else:
        if value is None:
            value = ''
        if not isinstance(value, str) or len(value) > _MAX_VALUE_LENGTH:
            _reject(f'field {name!r} must be a bounded string')
        populated = bool(value.strip())

    spans = _validate_spans(
        payload['spans'], narrative, f'field {name!r}', required=populated
    )
    confidence = payload.get('confidence', 0.0)
    if not isinstance(confidence, (int, float)) or not 0.0 <= float(confidence) <= 1.0:
        _reject(f'field {name!r}: confidence must be within [0, 1]')
    warnings = _validate_warnings(payload.get('warnings', []), f'field {name!r}')
    return {
        'value': value,
        'spans': spans,
        'confidence': float(confidence),
        'warnings': warnings,
    }


def _validate_candidates(candidates, allowed_keys, narrative: str, where: str):
    if not isinstance(candidates, list) or len(candidates) > _MAX_CANDIDATES:
        _reject(f'{where} must be a bounded list')
    validated = []
    for index, candidate in enumerate(candidates):
        label = f'{where}[{index}]'
        if not isinstance(candidate, dict):
            _reject(f'{label} must be an object')
        _require_no_identity_keys(candidate, label)
        extra = set(candidate) - allowed_keys
        if extra:
            _reject(f'{label} has unknown keys: {sorted(extra)}')
        text = candidate.get('text', '')
        if not isinstance(text, str) or not text.strip() or len(text) > 255:
            _reject(f'{label}: text is required and bounded')
        row = {
            'text': text,
            'spans': _validate_spans(
                candidate.get('spans', []), narrative, label, required=True
            ),
            'warnings': _validate_warnings(candidate.get('warnings', []), label),
        }
        for key in allowed_keys - {'text', 'spans', 'warnings'}:
            value = candidate.get(key, '')
            if not isinstance(value, str) or len(value) > 255:
                _reject(f'{label}: {key} must be a bounded string')
            row[key] = value
        validated.append(row)
    return validated


def validate_extraction_output(output, narrative: str) -> dict:
    """Validate one extractor document against the schema-v1 contract.

    Returns the normalized document. Raises ``ExtractionSchemaUnknown`` for a
    version this deployment does not understand and ``ExtractionInvalidOutput``
    for any structural violation, unanchored value, identity key, or oversized
    content. The output is data only; nothing in it is executed or resolved.
    """
    if not isinstance(output, dict):
        _reject('Extractor output must be a JSON object')
    extra = set(output) - _TOP_LEVEL_KEYS
    if extra:
        _reject(f'Extractor output has unknown keys: {sorted(extra)}')
    version = output.get('schema_version')
    if version != EXTRACTION_SCHEMA_VERSION:
        raise ExtractionSchemaUnknown(f'Unknown extraction schema version: {version!r}')

    raw_fields = output.get('fields')
    if not isinstance(raw_fields, dict):
        _reject('Extractor output requires a fields object')
    unknown_fields = set(raw_fields) - set(EXTRACTION_FIELDS)
    if unknown_fields:
        _reject(f'Extractor proposed unknown fields: {sorted(unknown_fields)}')

    fields = {
        name: _validate_field(name, raw_fields[name], narrative)
        for name in EXTRACTION_FIELDS
        if name in raw_fields
    }
    document = {
        'schema_version': EXTRACTION_SCHEMA_VERSION,
        'fields': fields,
        'part_candidates': _validate_candidates(
            output.get('part_candidates', []),
            _PART_CANDIDATE_KEYS,
            narrative,
            'part_candidates',
        ),
        'reading_candidates': _validate_candidates(
            output.get('reading_candidates', []),
            _READING_CANDIDATE_KEYS,
            narrative,
            'reading_candidates',
        ),
        'warnings': _validate_warnings(output.get('warnings', []), 'warnings'),
    }
    return document


NORMALIZATION_RULE_VERSION = 'co-norm-1'

_AMBIGUITY_MARKERS = ('–', '—', ' or ', ' to ', '/')  # noqa: RUF001 - dash variants are the detection targets


def normalize_reading(raw_text: str) -> tuple[Decimal | None, list[str]]:
    """Deterministically normalize one reading under rule set ``co-norm-1``.

    Returns ``(value, warnings)``. Ambiguous numerics (ranges, alternatives,
    multiple numbers, unparsable text) keep the raw text, return no value, and
    carry the ``numeric_ambiguity`` warning; a human must correct the source or
    enter an unambiguous manual value (FR-CO-009).
    """
    text = (raw_text or '').strip()
    if not text:
        return None, ['numeric_ambiguity']
    lowered = f' {text.lower()} '
    if any(marker in lowered for marker in _AMBIGUITY_MARKERS):
        return None, ['numeric_ambiguity']

    tokens = [
        token
        for token in text.replace(',', ' ').split()
        if any(char.isdigit() for char in token)
    ]
    if len(tokens) != 1:
        return None, ['numeric_ambiguity']
    candidate = tokens[0].strip('+')
    try:
        return Decimal(candidate), []
    except InvalidOperation:
        return None, ['numeric_ambiguity']
