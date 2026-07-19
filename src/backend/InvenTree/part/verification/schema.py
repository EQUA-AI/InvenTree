"""Closed typed-attribute schema, canonical serialization, and stable codes.

This module is the single authority for:

- closed enums shared by models, policy, services, and the API;
- the closed requirement value-kind / operator vocabulary;
- stable blocker and command error codes;
- canonical JSON serialization and domain-separated SHA-256 hashing.

Nothing here touches the ORM; model modules import from here, never the
reverse.
"""

import hashlib
import json
import unicodedata
from decimal import Decimal

from django.db import models
from django.utils.translation import gettext_lazy as _

SCHEMA_VERSION = 1

# Hash strings are stored as 'sha256:<64 hex chars>' (71 characters total)
HASH_PREFIX = 'sha256:'


class PartVerificationPurpose(models.TextChoices):
    """Closed set of verification purposes a session may bind."""

    INSTALLED_REPLACEMENT = 'installed_replacement', _('Installed Replacement')
    BOM_COMPONENT = 'bom_component', _('BOM Component')
    JOB_KIT_SUBSTITUTION = 'job_kit_substitution', _('Job Kit Substitution')
    RFQ_DEMAND = 'rfq_demand', _('RFQ Demand')
    PO_LINE = 'po_line', _('Purchase Order Line')
    MANUAL = 'manual', _('Manual Verification')


class PartVerificationState(models.TextChoices):
    """Closed session lifecycle states."""

    COLLECTING = 'collecting', _('Collecting')
    EVALUATING = 'evaluating', _('Evaluating')
    REVIEW_REQUIRED = 'review_required', _('Review Required')
    CONFIRMED = 'confirmed', _('Confirmed')
    NO_SAFE_MATCH = 'no_safe_match', _('No Safe Match')
    STALE = 'stale', _('Stale')
    CANCELLED = 'cancelled', _('Cancelled')


# States that permit an explicit cancel command
CANCELLABLE_STATES = frozenset({
    PartVerificationState.COLLECTING,
    PartVerificationState.EVALUATING,
    PartVerificationState.REVIEW_REQUIRED,
    PartVerificationState.STALE,
})

# Terminal states for the exact session revision
TERMINAL_STATES = frozenset({
    PartVerificationState.CONFIRMED,
    PartVerificationState.NO_SAFE_MATCH,
    PartVerificationState.CANCELLED,
})


class RequirementValueKind(models.TextChoices):
    """Closed set of canonical requirement value kinds."""

    TEXT = 'text', _('Text')
    IDENTIFIER = 'identifier', _('Identifier')
    DECIMAL = 'decimal', _('Decimal')
    RANGE = 'range', _('Range')
    SET = 'set', _('Set')
    BOOLEAN = 'boolean', _('Boolean')
    REVISION = 'revision', _('Revision')
    CERTIFICATION = 'certification', _('Certification')


class RequirementResolution(models.TextChoices):
    """Resolution state of a single typed requirement."""

    ACCEPTED = 'accepted', _('Accepted')
    MISSING = 'missing', _('Missing')
    CONFLICTING = 'conflicting', _('Conflicting')
    INVALID = 'invalid', _('Invalid')


class PolicyStatus(models.TextChoices):
    """Lifecycle status of an immutable policy version."""

    DRAFT = 'draft', _('Draft')
    ACTIVE = 'active', _('Active')
    RETIRED = 'retired', _('Retired')
    REVOKED = 'revoked', _('Revoked')


class EvidenceDecision(models.TextChoices):
    """Acceptance state of one captured evidence item."""

    PROPOSED = 'proposed', _('Proposed')
    ACCEPTED = 'accepted', _('Accepted')
    REJECTED = 'rejected', _('Rejected')
    SUPERSEDED = 'superseded', _('Superseded')


class DecisionKind(models.TextChoices):
    """Kind of a human verification decision."""

    CONFIRMED = 'confirmed', _('Confirmed')
    NO_SAFE_MATCH = 'no_safe_match', _('No Safe Match')


class HardResult(models.TextChoices):
    """Outcome of one hard-rule evaluation for one candidate attribute."""

    PASS = 'pass', _('Pass')
    CONFLICT = 'conflict', _('Conflict')
    MISSING = 'missing', _('Missing')
    INDETERMINATE = 'indeterminate', _('Indeterminate')


class DifferenceSeverity(models.TextChoices):
    """Severity classes for baseline/current revalidation differences.

    Ordered from most to least severe; the highest observed severity wins.
    Only NONE and explicitly policy-allowed NON_MATERIAL may create use.
    """

    BLOCKING = 'blocking', _('Blocking')
    INDETERMINATE_BLOCK = 'indeterminate_block', _('Indeterminate Block')
    MATERIAL_REVIEW = 'material_review', _('Material Review')
    NON_MATERIAL = 'non_material', _('Non Material')
    NONE = 'none', _('None')


# Severity precedence for revalidation (index 0 = most severe)
DIFFERENCE_SEVERITY_ORDER = [
    DifferenceSeverity.BLOCKING,
    DifferenceSeverity.INDETERMINATE_BLOCK,
    DifferenceSeverity.MATERIAL_REVIEW,
    DifferenceSeverity.NON_MATERIAL,
    DifferenceSeverity.NONE,
]


class EventType(models.TextChoices):
    """Append-only verification event types."""

    SESSION_CREATED = 'session_created', _('Session Created')
    EVIDENCE_ATTACHED = 'evidence_attached', _('Evidence Attached')
    EVIDENCE_DECIDED = 'evidence_decided', _('Evidence Decided')
    SESSION_EVALUATED = 'session_evaluated', _('Session Evaluated')
    EVALUATION_BLOCKED = 'evaluation_blocked', _('Evaluation Blocked')
    CANDIDATE_REJECTED = 'candidate_rejected', _('Candidate Rejected')
    SESSION_CONFIRMED = 'session_confirmed', _('Session Confirmed')
    NO_SAFE_MATCH_RECORDED = 'no_safe_match_recorded', _('No Safe Match Recorded')
    SESSION_STALE = 'session_stale', _('Session Stale')
    SESSION_REEVALUATED = 'session_reevaluated', _('Session Reevaluated')
    SESSION_CANCELLED = 'session_cancelled', _('Session Cancelled')
    USE_BOUND = 'use_bound', _('Use Bound')


# Closed comparison operators, and the value kinds each operator may bind
OPERATORS: dict[str, frozenset[str]] = {
    'eq': frozenset({
        RequirementValueKind.TEXT,
        RequirementValueKind.IDENTIFIER,
        RequirementValueKind.DECIMAL,
        RequirementValueKind.BOOLEAN,
        RequirementValueKind.REVISION,
    }),
    'in': frozenset({RequirementValueKind.SET}),
    'contains': frozenset({RequirementValueKind.SET}),
    'range_contains': frozenset({RequirementValueKind.RANGE}),
    'range_within': frozenset({RequirementValueKind.RANGE}),
    'gte': frozenset({RequirementValueKind.DECIMAL}),
    'lte': frozenset({RequirementValueKind.DECIMAL}),
    'present': frozenset({
        RequirementValueKind.TEXT,
        RequirementValueKind.IDENTIFIER,
        RequirementValueKind.BOOLEAN,
        RequirementValueKind.CERTIFICATION,
    }),
    'compatible_revision': frozenset({RequirementValueKind.REVISION}),
}


# Stable requirement/evaluation blocker codes (spec section 7.4)
class BlockerCodes:
    """Stable machine-readable blocker codes.

    Codes are stable API contract; localized text and remediation may evolve.
    """

    ASSET_REQUIRED = 'ASSET_REQUIRED'
    ASSET_POSITION_REQUIRED = 'ASSET_POSITION_REQUIRED'
    REQUESTED_PART_REQUIRED = 'REQUESTED_PART_REQUIRED'
    BOM_ITEM_REQUIRED = 'BOM_ITEM_REQUIRED'
    BOM_INVALID = 'BOM_INVALID'
    NAMEPLATE_REQUIRED = 'NAMEPLATE_REQUIRED'
    MANUAL_CONFIRMATION_REQUIRED = 'MANUAL_CONFIRMATION_REQUIRED'
    RATING_REQUIRED = 'RATING_REQUIRED'
    REQUIRED_ATTRIBUTE_MISSING = 'REQUIRED_ATTRIBUTE_MISSING'
    REQUIRED_ATTRIBUTE_INVALID = 'REQUIRED_ATTRIBUTE_INVALID'
    EVIDENCE_CONFLICT = 'EVIDENCE_CONFLICT'
    EVIDENCE_EXPIRED = 'EVIDENCE_EXPIRED'
    EVIDENCE_SCOPE_MISMATCH = 'EVIDENCE_SCOPE_MISMATCH'
    VISION_HANDOFF_REQUIRED = 'VISION_HANDOFF_REQUIRED'
    VISION_HANDOFF_STALE = 'VISION_HANDOFF_STALE'
    CANDIDATE_ATTRIBUTE_MISSING = 'CANDIDATE_ATTRIBUTE_MISSING'
    IDENTIFIER_AMBIGUOUS = 'IDENTIFIER_AMBIGUOUS'
    MANUFACTURER_MISMATCH = 'MANUFACTURER_MISMATCH'
    REVISION_CONFLICT = 'REVISION_CONFLICT'
    VOLTAGE_CONFLICT = 'VOLTAGE_CONFLICT'
    PHASE_CONFLICT = 'PHASE_CONFLICT'
    FREQUENCY_CONFLICT = 'FREQUENCY_CONFLICT'
    CURRENT_RATING_INSUFFICIENT = 'CURRENT_RATING_INSUFFICIENT'
    POWER_RATING_INSUFFICIENT = 'POWER_RATING_INSUFFICIENT'
    PRESSURE_RATING_INSUFFICIENT = 'PRESSURE_RATING_INSUFFICIENT'
    SPEED_RATING_CONFLICT = 'SPEED_RATING_CONFLICT'
    FIT_CONFLICT = 'FIT_CONFLICT'
    MOUNTING_CONFLICT = 'MOUNTING_CONFLICT'
    INTERFACE_CONFLICT = 'INTERFACE_CONFLICT'
    MATERIAL_CONFLICT = 'MATERIAL_CONFLICT'
    ENVIRONMENT_RATING_INSUFFICIENT = 'ENVIRONMENT_RATING_INSUFFICIENT'
    CERTIFICATION_REQUIRED = 'CERTIFICATION_REQUIRED'
    CERTIFICATION_INVALID = 'CERTIFICATION_INVALID'
    FIRMWARE_CONFLICT = 'FIRMWARE_CONFLICT'
    ORIENTATION_CONFLICT = 'ORIENTATION_CONFLICT'
    ASSET_POSITION_CONFLICT = 'ASSET_POSITION_CONFLICT'
    SUBSTITUTION_NOT_AUTHORIZED = 'SUBSTITUTION_NOT_AUTHORIZED'
    PART_INACTIVE = 'PART_INACTIVE'
    PART_SCOPE_MISMATCH = 'PART_SCOPE_MISMATCH'
    POLICY_UNAVAILABLE = 'POLICY_UNAVAILABLE'
    POLICY_REVOKED = 'POLICY_REVOKED'
    UNIT_UNSUPPORTED = 'UNIT_UNSUPPORTED'
    UNIT_DIMENSION_MISMATCH = 'UNIT_DIMENSION_MISMATCH'
    SEARCH_LIMIT_REACHED = 'SEARCH_LIMIT_REACHED'
    ATTRIBUTE_CONFLICT = 'ATTRIBUTE_CONFLICT'


# Stable command error codes (spec section 15.3)
class CommandCodes:
    """Stable machine-readable command/API error codes."""

    RPF_DISABLED = 'RPF_DISABLED'
    RPF_SCOPE_UNRESOLVED = 'RPF_SCOPE_UNRESOLVED'
    RPF_SCOPE_MISMATCH = 'RPF_SCOPE_MISMATCH'
    RPF_PERMISSION_DENIED = 'RPF_PERMISSION_DENIED'
    RPF_STATE_CONFLICT = 'RPF_STATE_CONFLICT'
    RPF_REVISION_CONFLICT = 'RPF_REVISION_CONFLICT'
    RPF_IDEMPOTENCY_CONFLICT = 'RPF_IDEMPOTENCY_CONFLICT'
    RPF_CONTEXT_CHANGED = 'RPF_CONTEXT_CHANGED'
    RPF_CONTEXT_INVALID = 'RPF_CONTEXT_INVALID'
    RPF_REQUIREMENTS_INCOMPLETE = 'RPF_REQUIREMENTS_INCOMPLETE'
    RPF_EVIDENCE_CONFLICT = 'RPF_EVIDENCE_CONFLICT'
    RPF_POLICY_UNAVAILABLE = 'RPF_POLICY_UNAVAILABLE'
    RPF_CANDIDATE_INELIGIBLE = 'RPF_CANDIDATE_INELIGIBLE'
    RPF_CANDIDATE_STALE = 'RPF_CANDIDATE_STALE'
    RPF_NO_SAFE_MATCH_INVALID = 'RPF_NO_SAFE_MATCH_INVALID'
    RPF_SESSION_STALE = 'RPF_SESSION_STALE'
    RPF_SESSION_EXPIRED = 'RPF_SESSION_EXPIRED'
    RPF_REVALIDATION_INDETERMINATE = 'RPF_REVALIDATION_INDETERMINATE'


# Stable consumer-facing blocker codes (spec section 13.3)
class ConsumerCodes:
    """Stable codes surfaced by downstream consumer preconditions."""

    PART_VERIFICATION_REQUIRED = 'PART_VERIFICATION_REQUIRED'
    PART_VERIFICATION_NOT_CONFIRMED = 'PART_VERIFICATION_NOT_CONFIRMED'
    PART_VERIFICATION_NO_SAFE_MATCH = 'PART_VERIFICATION_NO_SAFE_MATCH'
    PART_VERIFICATION_STALE = 'PART_VERIFICATION_STALE'
    PART_VERIFICATION_EXPIRED = 'PART_VERIFICATION_EXPIRED'
    PART_VERIFICATION_SCOPE_MISMATCH = 'PART_VERIFICATION_SCOPE_MISMATCH'
    PART_VERIFICATION_PURPOSE_MISMATCH = 'PART_VERIFICATION_PURPOSE_MISMATCH'
    PART_VERIFICATION_CONTEXT_MISMATCH = 'PART_VERIFICATION_CONTEXT_MISMATCH'
    PART_VERIFICATION_REQUESTED_PART_MISMATCH = (
        'PART_VERIFICATION_REQUESTED_PART_MISMATCH'
    )
    PART_VERIFICATION_SELECTED_PART_MISMATCH = (
        'PART_VERIFICATION_SELECTED_PART_MISMATCH'
    )
    PART_VERIFICATION_POLICY_INVALID = 'PART_VERIFICATION_POLICY_INVALID'
    PART_VERIFICATION_REVALIDATION_INDETERMINATE = (
        'PART_VERIFICATION_REVALIDATION_INDETERMINATE'
    )
    PART_VERIFICATION_USE_CONFLICT = 'PART_VERIFICATION_USE_CONFLICT'


class CanonicalizationError(ValueError):
    """Raised when a value cannot be canonically serialized."""


def _canonicalize(value):
    """Recursively convert a value into its canonical JSON-safe form.

    Rules (spec section 8.4): sorted object keys, Unicode NFC strings,
    fixed-point decimals rendered as strings, explicit booleans/None, and a
    hard prohibition on binary floats as decision authority.
    """
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        raise CanonicalizationError(
            'Binary float values are prohibited in canonical snapshots'
        )
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, str):
        return unicodedata.normalize('NFC', value)
    if isinstance(value, dict):
        out = {}
        for key in sorted(value.keys()):
            if not isinstance(key, str):
                raise CanonicalizationError('Canonical object keys must be strings')
            out[unicodedata.normalize('NFC', key)] = _canonicalize(value[key])
        return out
    if isinstance(value, (list, tuple)):
        return [_canonicalize(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_canonicalize(item) for item in value)
    raise CanonicalizationError(
        f'Value of type {type(value).__name__} cannot be canonicalized'
    )


def canonical_json(value) -> str:
    """Serialize a value to canonical JSON text.

    Deterministic across runs and platforms: sorted keys, no insignificant
    whitespace, NFC-normalized strings, and no floats.
    """
    return json.dumps(
        _canonicalize(value), separators=(',', ':'), ensure_ascii=False, sort_keys=True
    )


def hash_canonical(domain: str, value) -> str:
    """Hash a canonicalized value under a distinct named domain.

    Distinct SHA-256 domains keep requirement, evidence, evaluation, decision,
    source, availability, command, and observation hashes non-interchangeable.
    """
    payload = f'{domain}\x00{canonical_json(value)}'
    digest = hashlib.sha256(payload.encode('utf-8')).hexdigest()
    return f'{HASH_PREFIX}{digest}'


# Hash domains (spec section 8.4)
class HashDomains:
    """Distinct hash domains for each canonical snapshot family."""

    REQUIREMENTS = 'rpf.requirements'
    EVIDENCE = 'rpf.evidence'
    EVALUATION = 'rpf.evaluation'
    DECISION = 'rpf.decision'
    SOURCE = 'rpf.source'
    AVAILABILITY = 'rpf.availability'
    COMMAND = 'rpf.command'
    OBSERVATION = 'rpf.observation'
    SCOPE = 'rpf.scope'
    POLICY = 'rpf.policy'


def validate_operator(kind: str, operator: str) -> bool:
    """Return True when the operator is legal for the given value kind."""
    allowed = OPERATORS.get(operator)
    return allowed is not None and kind in allowed
