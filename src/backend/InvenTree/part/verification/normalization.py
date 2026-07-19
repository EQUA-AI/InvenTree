"""Namespace-aware identifier and unit/value canonicalization.

Identifier normalization is field-specific (spec section 9.2): surrounding
trim plus a configured case rule only. Punctuation, revision suffixes, and
leading zeros are always preserved as significant.

Physical values are parsed through the repository unit registry
(``InvenTree.conversion``) and canonicalized to fixed-point decimal strings in
the policy unit. Binary floats never become decision authority; the raw value
and unit are preserved beside every canonical value.
"""

import unicodedata
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

from django.core.exceptions import ValidationError

import InvenTree.conversion
from part.verification.schema import BlockerCodes

# Identifier namespaces that compare case-insensitively by default
_CASEFOLD_NAMESPACES = frozenset({'ipn', 'mpn', 'sku'})

# Default quantization for canonical decimal values (6 fractional digits)
DEFAULT_DECIMAL_PLACES = 6


class NormalizationError(ValueError):
    """Raised when a value cannot be normalized safely.

    Carries a stable blocker code so callers can surface the failure without
    interpreting message text.
    """

    def __init__(
        self, message: str, code: str = BlockerCodes.REQUIRED_ATTRIBUTE_INVALID
    ):
        """Store the stable blocker code alongside the message."""
        super().__init__(message)
        self.code = code


def normalize_identifier(namespace: str, value: str) -> dict:
    """Normalize an identifier within its namespace.

    Returns a dict with the namespace, the preserved raw value, and the
    normalized comparison value. Only trims surrounding whitespace and applies
    the namespace case rule; every other character is significant.
    """
    if value is None:
        raise NormalizationError('Identifier value is required')

    raw = str(value)
    normalized = unicodedata.normalize('NFC', raw.strip())

    if not normalized:
        raise NormalizationError('Identifier value is blank')

    if namespace.lower() in _CASEFOLD_NAMESPACES:
        normalized = normalized.casefold()

    return {'namespace': namespace.lower(), 'raw': raw, 'normalized': normalized}


def canonical_decimal(
    value,
    unit: str = '',
    target_unit: str = '',
    decimal_places: int = DEFAULT_DECIMAL_PLACES,
) -> str:
    """Canonicalize a physical or plain numeric value to a decimal string.

    When a target unit is declared, the value (with its source unit, if any)
    is converted through the repository unit registry and quantized at the
    declared precision with banker's rounding. Dimension mismatches and
    unsupported units raise ``NormalizationError`` with a stable code.
    """
    if value is None:
        raise NormalizationError('Numeric value is required')

    text = str(value).strip()
    if not text:
        raise NormalizationError('Numeric value is blank')

    if target_unit:
        source = f'{text} {unit}'.strip() if unit else text
        try:
            converted = InvenTree.conversion.convert_physical_value(
                source, target_unit, strip_units=True
            )
        except ValidationError as exc:
            message = str(exc.message) if hasattr(exc, 'message') else str(exc)
            code = BlockerCodes.UNIT_DIMENSION_MISMATCH
            if 'Invalid unit' in message:
                code = BlockerCodes.UNIT_UNSUPPORTED
            raise NormalizationError(
                f'Cannot convert value to {target_unit}: {message}', code=code
            ) from exc
        text = str(converted)

    try:
        quantum = Decimal(1).scaleb(-decimal_places)
        result = Decimal(text).quantize(quantum, rounding=ROUND_HALF_EVEN)
    except InvalidOperation as exc:
        raise NormalizationError(f'Value is not a valid number: {text}') from exc

    return str(result)


def canonical_range(
    value: dict,
    unit: str = '',
    target_unit: str = '',
    decimal_places: int = DEFAULT_DECIMAL_PLACES,
) -> dict:
    """Canonicalize a range value with explicit bounds.

    Absent bounds stay explicitly ``None``. Present bounds are canonicalized
    like decimals; an inverted range fails.
    """
    if not isinstance(value, dict):
        raise NormalizationError('Range value must be an object with min/max bounds')

    result = {'min': None, 'max': None}
    for bound in ('min', 'max'):
        raw = value.get(bound)
        if raw is not None:
            result[bound] = canonical_decimal(
                raw, unit=unit, target_unit=target_unit, decimal_places=decimal_places
            )

    if (
        result['min'] is not None
        and result['max'] is not None
        and Decimal(result['min']) > Decimal(result['max'])
    ):
        raise NormalizationError('Range minimum exceeds range maximum')

    return result


def canonical_set(values, *, casefold: bool = False) -> list:
    """Canonicalize a set value into sorted deduplicated members."""
    if isinstance(values, (str, bytes)) or not hasattr(values, '__iter__'):
        raise NormalizationError('Set value must be a list of members')

    members = set()
    for item in values:
        text = unicodedata.normalize('NFC', str(item).strip())
        if not text:
            continue
        members.add(text.casefold() if casefold else text)

    if not members:
        raise NormalizationError('Set value has no members')

    return sorted(members)


def canonical_boolean(value) -> bool:
    """Canonicalize a strict boolean value.

    Only JSON booleans and the exact strings 'true'/'false' are accepted;
    blank, 'yes', and numeric forms never convert implicitly.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().casefold()
        if text == 'true':
            return True
        if text == 'false':
            return False
    raise NormalizationError('Boolean value must be true or false')


def canonical_value(
    kind: str,
    value,
    *,
    unit: str = '',
    target_unit: str = '',
    decimal_places: int = DEFAULT_DECIMAL_PLACES,
    identifier_namespace: str = '',
):
    """Canonicalize a value according to its closed requirement kind."""
    from part.verification.schema import RequirementValueKind

    if kind == RequirementValueKind.DECIMAL:
        return canonical_decimal(
            value, unit=unit, target_unit=target_unit, decimal_places=decimal_places
        )
    if kind == RequirementValueKind.RANGE:
        return canonical_range(
            value, unit=unit, target_unit=target_unit, decimal_places=decimal_places
        )
    if kind == RequirementValueKind.SET:
        return canonical_set(value)
    if kind == RequirementValueKind.BOOLEAN:
        return canonical_boolean(value)
    if kind == RequirementValueKind.IDENTIFIER:
        return normalize_identifier(identifier_namespace or 'generic', value)[
            'normalized'
        ]
    if kind in (
        RequirementValueKind.TEXT,
        RequirementValueKind.REVISION,
        RequirementValueKind.CERTIFICATION,
    ):
        if value is None:
            raise NormalizationError('Value is required')
        if kind == RequirementValueKind.CERTIFICATION and isinstance(value, dict):
            return {
                key: unicodedata.normalize('NFC', str(item))
                for key, item in sorted(value.items())
            }
        return unicodedata.normalize('NFC', str(value).strip())
    raise NormalizationError(f'Unsupported requirement value kind: {kind}')
