"""Immutable verification policy loading, validation, and lifecycle.

A policy version is a closed, versioned JSON document. Once activated its
definition is immutable; weight or rule changes require a new version, and
revocation makes dependent decisions unusable rather than rewriting history.

No arbitrary executable content is ever stored as policy (spec section 2.3):
the document is validated against a closed vocabulary before use.
"""

from django.conf import settings
from django.utils import timezone

from part.verification.schema import (
    OPERATORS,
    BlockerCodes,
    HashDomains,
    PolicyStatus,
    RequirementValueKind,
    hash_canonical,
    validate_operator,
)

# Default policy selection when settings do not override it
DEFAULT_POLICY_KEY = 'rpf-core'
DEFAULT_POLICY_VERSION = 1

# Closed set of visible rank factor identifiers (spec section 10.1)
KNOWN_RANK_FACTORS = frozenset({
    'exact_requested_identity',
    'exact_application_relation',
    'revision_preference',
    'evidence_coverage',
    'asset_history_relevance',
    'catalog_completeness',
    'preferred_representation',
    'freshness',
})

# Closed set of requirement-side source kinds
KNOWN_SOURCE_KINDS = frozenset({'observation', 'parameter', 'machine', 'bom'})

# Closed set of candidate-side source kinds
KNOWN_CANDIDATE_SOURCE_KINDS = frozenset({'parameter', 'field'})

# Candidate identity fields a policy may bind directly
KNOWN_CANDIDATE_FIELDS = frozenset({'IPN', 'name', 'revision'})

# Closed candidate-missing behaviors; neither is ever a wildcard pass
CANDIDATE_MISSING_BEHAVIORS = frozenset({'exclude', 'indeterminate'})

_KNOWN_TOP_KEYS = frozenset({
    'schema_version',
    'description',
    'requirements',
    'retrieval',
    'rank_factors',
    'revalidation',
})

_KNOWN_REQUIREMENT_KEYS = frozenset({
    'key',
    'category',
    'value_kind',
    'operator',
    'unit',
    'hard',
    'decimal_places',
    'tolerance',
    'identifier_namespace',
    'sources',
    'candidate_sources',
    'missing_blocker',
    'candidate_missing',
    'conflict_code',
})

_BLOCKER_CODES = frozenset(
    value
    for name, value in vars(BlockerCodes).items()
    if not name.startswith('_') and isinstance(value, str)
)


class PolicyError(Exception):
    """Raised when a policy document is invalid or unavailable.

    Carries a stable code (``POLICY_UNAVAILABLE`` or ``POLICY_REVOKED``).
    """

    def __init__(self, message: str, code: str = BlockerCodes.POLICY_UNAVAILABLE):
        """Store the stable code alongside the message."""
        super().__init__(message)
        self.code = code


def policy_hash(definition: dict) -> str:
    """Return the canonical hash of a policy definition."""
    return hash_canonical(HashDomains.POLICY, definition)


def _validate_requirement(entry: dict, index: int):
    """Validate one policy requirement entry against the closed vocabulary."""
    prefix = f'requirements[{index}]'

    if not isinstance(entry, dict):
        raise PolicyError(f'{prefix} must be an object')

    unknown = set(entry.keys()) - _KNOWN_REQUIREMENT_KEYS
    if unknown:
        raise PolicyError(f'{prefix} has unknown keys: {sorted(unknown)}')

    key = entry.get('key')
    if not key or not isinstance(key, str):
        raise PolicyError(f'{prefix} requires a stable string key')

    kind = entry.get('value_kind')
    if kind not in RequirementValueKind.values:
        raise PolicyError(f'{prefix} has unsupported value_kind: {kind}')

    operator = entry.get('operator')
    if operator not in OPERATORS or not validate_operator(kind, operator):
        raise PolicyError(f'{prefix} operator {operator} is not legal for kind {kind}')

    if not isinstance(entry.get('hard', True), bool):
        raise PolicyError(f'{prefix} hard flag must be boolean')

    missing_blocker = entry.get(
        'missing_blocker', BlockerCodes.REQUIRED_ATTRIBUTE_MISSING
    )
    if missing_blocker not in _BLOCKER_CODES:
        raise PolicyError(f'{prefix} has unknown missing_blocker: {missing_blocker}')

    conflict_code = entry.get('conflict_code')
    if conflict_code is not None and conflict_code not in _BLOCKER_CODES:
        raise PolicyError(f'{prefix} has unknown conflict_code: {conflict_code}')

    behavior = entry.get('candidate_missing', 'exclude')
    if behavior not in CANDIDATE_MISSING_BEHAVIORS:
        raise PolicyError(f'{prefix} has unknown candidate_missing: {behavior}')

    tolerance = entry.get('tolerance')
    if tolerance is not None:
        if not isinstance(tolerance, dict) or tolerance.get('kind') not in (
            'absolute',
            'percent',
        ):
            raise PolicyError(
                f'{prefix} tolerance must declare an absolute/percent kind'
            )
        # Fixed-point decimal strings or integers only; binary floats are
        # prohibited in canonical policy content
        if not isinstance(tolerance.get('value'), (str, int)):
            raise PolicyError(f'{prefix} tolerance value must be a decimal string')

    sources = entry.get('sources', [])
    if not isinstance(sources, list) or not sources:
        raise PolicyError(f'{prefix} requires an ordered non-empty sources list')
    for si, source in enumerate(sources):
        if not isinstance(source, dict) or source.get('kind') not in KNOWN_SOURCE_KINDS:
            raise PolicyError(f'{prefix}.sources[{si}] has unsupported kind')
        if source['kind'] == 'parameter' and not source.get('template'):
            raise PolicyError(f'{prefix}.sources[{si}] requires a template name')
        if source['kind'] == 'machine' and not source.get('field'):
            raise PolicyError(f'{prefix}.sources[{si}] requires a machine field')

    for si, source in enumerate(entry.get('candidate_sources', [])):
        if (
            not isinstance(source, dict)
            or source.get('kind') not in KNOWN_CANDIDATE_SOURCE_KINDS
        ):
            raise PolicyError(f'{prefix}.candidate_sources[{si}] has unsupported kind')
        if source['kind'] == 'parameter' and not source.get('template'):
            raise PolicyError(
                f'{prefix}.candidate_sources[{si}] requires a template name'
            )
        if (
            source['kind'] == 'field'
            and source.get('field') not in KNOWN_CANDIDATE_FIELDS
        ):
            raise PolicyError(f'{prefix}.candidate_sources[{si}] has unsupported field')


def validate_definition(definition: dict):
    """Validate a policy definition document against the closed vocabulary.

    Raises ``PolicyError`` on the first violation; a valid document returns
    silently.
    """
    if not isinstance(definition, dict):
        raise PolicyError('Policy definition must be an object')

    unknown = set(definition.keys()) - _KNOWN_TOP_KEYS
    if unknown:
        raise PolicyError(f'Policy definition has unknown keys: {sorted(unknown)}')

    if definition.get('schema_version') != 1:
        raise PolicyError('Policy schema_version must be 1')

    requirements = definition.get('requirements')
    if not isinstance(requirements, list) or not requirements:
        raise PolicyError('Policy definition requires a non-empty requirements list')

    seen_keys = set()
    for index, entry in enumerate(requirements):
        _validate_requirement(entry, index)
        if entry['key'] in seen_keys:
            raise PolicyError(f'Duplicate requirement key: {entry["key"]}')
        seen_keys.add(entry['key'])

    retrieval = definition.get('retrieval', {})
    if not isinstance(retrieval, dict):
        raise PolicyError('Policy retrieval section must be an object')
    for cap_key in ('max_candidates', 'tier_cap'):
        cap = retrieval.get(cap_key)
        if cap is not None and (not isinstance(cap, int) or cap < 1):
            raise PolicyError(f'Policy retrieval.{cap_key} must be a positive integer')

    factors = definition.get('rank_factors', [])
    if not isinstance(factors, list):
        raise PolicyError('Policy rank_factors must be a list')
    seen_factors = set()
    for index, factor in enumerate(factors):
        if not isinstance(factor, dict) or factor.get('id') not in KNOWN_RANK_FACTORS:
            raise PolicyError(f'rank_factors[{index}] has unknown factor id')
        maximum = factor.get('max')
        if not isinstance(maximum, int) or maximum < 0:
            raise PolicyError(f'rank_factors[{index}].max must be a non-negative int')
        if factor['id'] in seen_factors:
            raise PolicyError(f'Duplicate rank factor: {factor["id"]}')
        seen_factors.add(factor['id'])

    revalidation = definition.get('revalidation', {})
    if not isinstance(revalidation, dict):
        raise PolicyError('Policy revalidation section must be an object')
    paths = revalidation.get('non_material_paths', [])
    if not isinstance(paths, list) or any(not isinstance(p, str) for p in paths):
        raise PolicyError('Policy non_material_paths must be a list of strings')
    expiry = revalidation.get('expiry_hours')
    if expiry is not None and (not isinstance(expiry, int) or not 1 <= expiry <= 720):
        raise PolicyError('Policy expiry_hours must be an integer within 1..720')


def create_policy_version(
    *, key: str, version: int, definition: dict, actor=None, activate: bool = False
):
    """Create (and optionally activate) a policy version from a reviewed document."""
    from part.verification_models import PartVerificationPolicyVersion

    validate_definition(definition)

    policy = PartVerificationPolicyVersion.objects.create(
        key=key,
        version=version,
        definition=definition,
        definition_hash=policy_hash(definition),
        created_by=actor,
    )

    if activate:
        activate_policy(policy, actor=actor)

    return policy


def activate_policy(policy, actor=None):
    """Activate a draft policy version."""
    if policy.status != PolicyStatus.DRAFT:
        raise PolicyError(f'Policy {policy} cannot be activated from {policy.status}')
    validate_definition(policy.definition)
    policy.status = PolicyStatus.ACTIVE
    policy.effective_from = timezone.now()
    policy.activated_by = actor
    policy.save()
    return policy


def revoke_policy(policy):
    """Revoke a policy version, preserving its definition and history."""
    policy.status = PolicyStatus.REVOKED
    policy.effective_until = timezone.now()
    policy.save()
    return policy


def load_active_policy():
    """Load and validate the configured active policy version.

    Fails closed with ``PolicyError`` when the configured version is missing,
    not active, hash-mismatched, or invalid.
    """
    from part.verification_models import PartVerificationPolicyVersion

    key = getattr(settings, 'AIMMS_RPF_POLICY_KEY', DEFAULT_POLICY_KEY)
    version = getattr(settings, 'AIMMS_RPF_POLICY_VERSION', DEFAULT_POLICY_VERSION)

    policy = PartVerificationPolicyVersion.objects.filter(
        key=key, version=version
    ).first()

    if policy is None:
        raise PolicyError(f'No policy version {key} v{version} exists')

    if policy.status == PolicyStatus.REVOKED:
        raise PolicyError(
            f'Policy {key} v{version} is revoked', code=BlockerCodes.POLICY_REVOKED
        )

    if policy.status != PolicyStatus.ACTIVE:
        raise PolicyError(f'Policy {key} v{version} is not active')

    if policy.definition_hash != policy_hash(policy.definition):
        raise PolicyError(f'Policy {key} v{version} definition hash mismatch')

    validate_definition(policy.definition)

    return policy


def policy_requirements(policy) -> list[dict]:
    """Return the validated requirement entries of a policy."""
    return list(policy.definition.get('requirements', []))


def retrieval_limits(policy) -> tuple[int, int]:
    """Return (max_candidates, tier_cap) for retrieval, honoring settings caps."""
    retrieval = policy.definition.get('retrieval', {})
    setting_cap = getattr(settings, 'AIMMS_RPF_MAX_CANDIDATES', 100)
    max_candidates = min(retrieval.get('max_candidates', setting_cap), setting_cap)
    tier_cap = retrieval.get('tier_cap', max_candidates)
    return max_candidates, min(tier_cap, max_candidates)


def rank_factor_weights(policy) -> list[dict]:
    """Return the ordered rank factor weight entries of a policy."""
    return list(policy.definition.get('rank_factors', []))


def non_material_paths(policy) -> frozenset[str]:
    """Return the allowlisted non-material difference paths of a policy."""
    return frozenset(
        policy.definition.get('revalidation', {}).get('non_material_paths', [])
    )


def expiry_hours(policy) -> int:
    """Return the session/decision expiry window in hours."""
    configured = policy.definition.get('revalidation', {}).get('expiry_hours')
    if configured is not None:
        return configured
    return getattr(settings, 'AIMMS_RPF_SESSION_TTL_HOURS', 24)
