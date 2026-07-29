"""Baseline/current observation comparison and staleness classification.

Reuses the Drift Protection vocabulary (RPF-ADR-012): a baseline is the
immutable decision snapshot, a current observation is the same projection
rebuilt now, and typed path-level differences classify into a fail-closed
severity precedence where only NONE and policy-allowed NON_MATERIAL permit
use (spec section 14).
"""

from dataclasses import dataclass, field

from part.verification import policy as policy_module
from part.verification.schema import (
    DIFFERENCE_SEVERITY_ORDER,
    DifferenceSeverity,
    PolicyStatus,
)

# Sentinel for absent values in path diffs
_ABSENT = object()


@dataclass
class RevalidationResult:
    """Classified outcome of one baseline/current comparison."""

    severity: str = DifferenceSeverity.NONE
    differences: list = field(default_factory=list)

    @property
    def usable(self) -> bool:
        """True when the result alone permits downstream use."""
        return self.severity in (
            DifferenceSeverity.NONE,
            DifferenceSeverity.NON_MATERIAL,
        )


def _flatten(value, prefix=''):
    """Flatten a nested observation into dotted path/value pairs.

    Yields:
        tuple: ``(path, value)`` pairs for every leaf of the observation.
    """
    if isinstance(value, dict):
        for key in sorted(value.keys()):
            yield from _flatten(value[key], f'{prefix}{key}.' if prefix else f'{key}.')
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            yield from _flatten(item, f'{prefix}{index}.')
        return
    yield prefix.rstrip('.'), value


def diff_observations(baseline: dict, current: dict) -> list[dict]:
    """Return path-level differences between two observations."""
    base_map = dict(_flatten(baseline))
    current_map = dict(_flatten(current))

    differences = []
    for path in sorted(set(base_map) | set(current_map)):
        before = base_map.get(path, _ABSENT)
        after = current_map.get(path, _ABSENT)
        if before != after:
            differences.append({
                'path': path,
                'baseline': None if before is _ABSENT else before,
                'current': None if after is _ABSENT else after,
            })
    return differences


# Path fragments whose change is blocking regardless of policy allowlists.
# 'machine.customer' is retained deliberately: a baseline snapshotted before
# machines lost their customer field must block (the field disappearing IS an
# ownership change), not silently pass revalidation.
_BLOCKING_FRAGMENTS = (
    'scope_fingerprint',
    'policy.status',
    'policy.hash',
    'bom_item.sub_part',
    'bom_item.id',
    'machine.customer',
    'machine.client',
)


def _classify_path(path: str, difference: dict, allowlist: frozenset) -> str:
    """Classify one difference path into a severity."""
    for fragment in _BLOCKING_FRAGMENTS:
        if path == fragment or path.endswith(f'.{fragment}'):
            return DifferenceSeverity.BLOCKING

    # A part becoming inactive or locked is blocking
    if path.endswith('.active') and difference['current'] is False:
        return DifferenceSeverity.BLOCKING
    if path.endswith('.locked') and difference['current'] is True:
        return DifferenceSeverity.BLOCKING

    if path in allowlist:
        return DifferenceSeverity.NON_MATERIAL

    return DifferenceSeverity.MATERIAL_REVIEW


def classify(baseline: dict, current: dict, policy) -> RevalidationResult:
    """Compare and classify a baseline against a current observation.

    The highest severity wins (spec section 14.1). A policy that is no longer
    ACTIVE is blocking even when the stored snapshot text matches.
    """
    result = RevalidationResult()

    try:
        allowlist = policy_module.non_material_paths(policy)
        differences = diff_observations(baseline, current)
    except Exception:
        result.severity = DifferenceSeverity.INDETERMINATE_BLOCK
        return result

    severities = []

    if policy.status != PolicyStatus.ACTIVE:
        severities.append(DifferenceSeverity.BLOCKING)
        differences.append({
            'path': 'policy.status',
            'baseline': PolicyStatus.ACTIVE.value,
            'current': policy.status,
        })

    for difference in differences:
        severity = _classify_path(difference['path'], difference, allowlist)
        difference['severity'] = severity
        severities.append(severity)

    result.differences = differences

    for severity in DIFFERENCE_SEVERITY_ORDER:
        if severity in severities:
            result.severity = severity
            break

    return result
