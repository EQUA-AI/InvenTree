"""Versioned schema + validation for the Repair Packet diagnosis blob.

The diagnosis is produced by the AI generation layer (``repair.generation``) and
persisted as JSON on :class:`repair.models.RepairPacket`. Keeping the contract in
one place lets the serializer, services and generators agree on a single shape
and lets us evolve it with an explicit version number rather than silent drift.
"""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError

# Bump when the diagnosis shape changes in a backwards-incompatible way.
DIAGNOSIS_SCHEMA_VERSION = 1

# key -> accepted python type(s)
_REQUIRED_FIELDS: dict[str, type | tuple[type, ...]] = {
    'likely_cause': str,
    'confidence': (int, float),
    'alternatives': list,
    'evidence': list,
    'confirm_tests': list,
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


def coerce_diagnosis(data: dict[str, Any] | None) -> dict[str, Any]:
    """Best-effort normalise an arbitrary diagnosis dict into the schema.

    Fills missing keys with safe defaults, clamps ``confidence`` into ``0..1``,
    derives ``confidence_label`` and stamps the schema version. Never raises -
    use :func:`validate_diagnosis` when strict checking is required.
    """
    result = empty_diagnosis()
    if isinstance(data, dict):
        for key in ('likely_cause', 'failure_mode'):
            if data.get(key) is not None:
                result[key] = data[key]
        for key in ('alternatives', 'evidence', 'confirm_tests'):
            value = data.get(key)
            if isinstance(value, list):
                result[key] = value
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
    result['confidence_label'] = confidence_label(result['confidence'])
    result['schema_version'] = DIAGNOSIS_SCHEMA_VERSION
    return result


def validate_diagnosis(data: Any) -> None:
    """Strictly validate a diagnosis blob, raising ``ValidationError`` if bad."""
    if not isinstance(data, dict):
        raise ValidationError('Diagnosis must be a JSON object.')

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
