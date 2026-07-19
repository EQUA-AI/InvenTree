"""Deterministic typed requirement construction.

Builds the current typed requirement set for a session from policy-declared
sources with field-specific precedence (spec section 8). Missing or
conflicting hard facts produce stable blockers and never act as wildcards.
"""

from dataclasses import dataclass, field

from django.utils.translation import gettext_lazy as _

from part.verification import policy as policy_module
from part.verification import sources as sources_module
from part.verification.normalization import NormalizationError, canonical_value
from part.verification.schema import (
    BlockerCodes,
    HashDomains,
    PartVerificationPurpose,
    RequirementResolution,
    RequirementValueKind,
    hash_canonical,
)


@dataclass
class Blocker:
    """One stable, remediable blocker."""

    code: str
    attribute: str = ''
    message: str = ''
    remediation: str = ''

    def as_dict(self) -> dict:
        """Return the API projection of this blocker."""
        return {
            'code': self.code,
            'attribute': self.attribute,
            'message': str(self.message),
            'remediation': str(self.remediation),
        }


@dataclass
class RequirementBuild:
    """Result of deterministic requirement construction."""

    requirements: list = field(default_factory=list)
    blockers: list = field(default_factory=list)
    requirements_hash: str = ''

    @property
    def blocked(self) -> bool:
        """True when any hard blocker prevents complete evaluation."""
        return bool(self.blockers)


def validate_context(session) -> list[Blocker]:
    """Validate the purpose-specific required context of a session.

    Implements the required-context table of spec section 5.3. Ambiguous
    installed-position context blocks rather than guessing.
    """
    blockers: list[Blocker] = []
    purpose = session.purpose

    def _require(condition, code, attribute, message, remediation=''):
        if not condition:
            blockers.append(
                Blocker(
                    code=code,
                    attribute=attribute,
                    message=message,
                    remediation=remediation,
                )
            )

    if purpose == PartVerificationPurpose.INSTALLED_REPLACEMENT:
        _require(
            session.machine_id is not None,
            BlockerCodes.ASSET_REQUIRED,
            'machine',
            _('An exact asset machine is required for installed replacement'),
        )
        _require(
            session.requested_part_id is not None,
            BlockerCodes.REQUESTED_PART_REQUIRED,
            'requested_part',
            _('The requested part identity is required'),
        )
        if session.machine_id is not None and session.requested_part_id is not None:
            if session.machine_part_id is not None:
                position_ok = (
                    session.machine_part.machine_id == session.machine_id
                    and session.machine_part.part_id == session.requested_part_id
                )
                _require(
                    position_ok,
                    BlockerCodes.ASSET_POSITION_REQUIRED,
                    'machine_part',
                    _('The installed-part row does not match the session context'),
                )
            else:
                installed = session.machine.machine_parts.filter(
                    part=session.requested_part
                ).count()
                _require(
                    installed == 1,
                    BlockerCodes.ASSET_POSITION_REQUIRED,
                    'machine_part',
                    _('The installed position is ambiguous or unknown'),
                    _('Bind the exact installed-part row for this machine'),
                )
    elif purpose == PartVerificationPurpose.BOM_COMPONENT:
        _require(
            session.bom_item_id is not None,
            BlockerCodes.BOM_ITEM_REQUIRED,
            'bom_item',
            _('An exact BOM line is required for BOM component verification'),
        )
        if session.bom_item_id is not None:
            _require(
                session.requested_part_id == session.bom_item.sub_part_id,
                BlockerCodes.BOM_INVALID,
                'requested_part',
                _('The requested part must equal the current BOM line component'),
            )
    elif purpose == PartVerificationPurpose.JOB_KIT_SUBSTITUTION:
        _require(
            session.work_order_id is not None,
            BlockerCodes.REQUESTED_PART_REQUIRED,
            'work_order',
            _('The owning work order is required for Job Kit substitution'),
        )
        _require(
            session.job_kit_line_id is not None,
            BlockerCodes.REQUESTED_PART_REQUIRED,
            'job_kit_line',
            _('The exact Job Kit line is required for Job Kit substitution'),
        )
        if session.job_kit_line_id is not None:
            _require(
                session.requested_part_id == session.job_kit_line.requested_part_id,
                BlockerCodes.REQUESTED_PART_REQUIRED,
                'requested_part',
                _('The requested part must equal the Job Kit line requested part'),
            )
    else:
        _require(
            session.requested_part_id is not None,
            BlockerCodes.REQUESTED_PART_REQUIRED,
            'requested_part',
            _('The requested part identity is required'),
        )

    return blockers


def _canonicalize_fact(spec: dict, fact) -> object:
    """Canonicalize one source fact value for a requirement spec."""
    kind = spec['value_kind']
    raw = fact.raw_value

    # A scalar fact satisfies a range requirement as a degenerate range
    if kind == RequirementValueKind.RANGE and not isinstance(raw, dict):
        raw = {'min': raw, 'max': raw}

    return canonical_value(
        kind,
        raw,
        unit=fact.unit,
        target_unit=spec.get('unit', ''),
        decimal_places=spec.get('decimal_places', 6),
        identifier_namespace=spec.get('identifier_namespace', ''),
    )


def _resolve_requirement(session, spec: dict) -> dict:
    """Resolve one policy requirement entry from its declared sources.

    Source precedence is the declared list order; the first authority class
    producing at least one fact wins. Equal-authority contradictions block
    (spec section 8.3) and are never averaged or voted away.
    """
    key = spec['key']
    hard = spec.get('hard', True)

    resolution = RequirementResolution.MISSING
    blocker_code = ''
    value = None
    raw_value = None
    authority = ''
    provenance: list[dict] = []

    for source_spec in spec['sources']:
        facts = sources_module.facts_for_source(session, source_spec, key)
        if not facts:
            continue

        canonical_values = []
        errors = []
        for fact in facts:
            try:
                canonical_values.append(_canonicalize_fact(spec, fact))
            except NormalizationError as error:
                errors.append(error)

        provenance = [fact.provenance() for fact in facts]
        authority = facts[0].authority
        raw_value = facts[0].raw_value

        if errors and not canonical_values:
            resolution = RequirementResolution.INVALID
            blocker_code = errors[0].code
            break

        distinct = {
            hash_canonical(HashDomains.REQUIREMENTS, v) for v in canonical_values
        }
        if len(distinct) > 1:
            resolution = RequirementResolution.CONFLICTING
            blocker_code = BlockerCodes.EVIDENCE_CONFLICT
            break

        resolution = RequirementResolution.ACCEPTED
        value = canonical_values[0]
        break

    if resolution == RequirementResolution.MISSING and hard:
        blocker_code = spec.get(
            'missing_blocker', BlockerCodes.REQUIRED_ATTRIBUTE_MISSING
        )

    return {
        'key': key,
        'category': spec.get('category', ''),
        'value_kind': spec['value_kind'],
        'operator': spec['operator'],
        'unit': spec.get('unit', ''),
        'tolerance': spec.get('tolerance', {}),
        'hard_constraint': hard,
        'resolution': resolution,
        'blocker_code': blocker_code,
        'value': value,
        'raw_value': raw_value,
        'authority': authority,
        'provenance': provenance,
    }


def build_requirements(session, policy) -> RequirementBuild:
    """Build and persist the current typed requirement set for a session.

    Returns the built requirements, hard blockers, and the canonical
    requirements hash. Persisted rows are upserted per (session, key); keys no
    longer declared by policy are removed.
    """
    from part.verification_models import PartVerificationRequirement

    build = RequirementBuild()

    context_blockers = validate_context(session)
    if context_blockers:
        build.blockers = [blocker.as_dict() for blocker in context_blockers]
        return build

    specs = policy_module.policy_requirements(policy)
    seen_keys = []

    for spec in specs:
        resolved = _resolve_requirement(session, spec)
        seen_keys.append(resolved['key'])

        if resolved['hard_constraint'] and resolved['blocker_code']:
            build.blockers.append(
                Blocker(
                    code=resolved['blocker_code'],
                    attribute=resolved['key'],
                    message=str(_('Required fact is missing, invalid, or conflicting')),
                    remediation=str(
                        _('Attach or accept authoritative evidence for this attribute')
                    ),
                ).as_dict()
            )

        PartVerificationRequirement.objects.update_or_create(
            session=session,
            key=resolved['key'],
            defaults={
                'category': resolved['category'],
                'value_kind': resolved['value_kind'],
                'operator': resolved['operator'],
                'unit': resolved['unit'],
                'tolerance': resolved['tolerance'],
                'hard_constraint': resolved['hard_constraint'],
                'resolution': resolved['resolution'],
                'blocker_code': resolved['blocker_code'],
                'value': resolved['value'],
                'raw_value': _raw_jsonable(resolved['raw_value']),
                'authority': resolved['authority'],
                'provenance': resolved['provenance'],
            },
        )
        build.requirements.append(resolved)

    session.requirements.exclude(key__in=seen_keys).delete()

    build.requirements_hash = hash_canonical(
        HashDomains.REQUIREMENTS,
        [
            {
                'key': item['key'],
                'value_kind': item['value_kind'],
                'operator': item['operator'],
                'unit': item['unit'],
                'tolerance': item['tolerance'],
                'hard_constraint': item['hard_constraint'],
                'resolution': item['resolution'],
                'value': item['value'],
            }
            for item in build.requirements
        ],
    )

    return build


def _raw_jsonable(value):
    """Coerce a raw fact value into a JSON-storable form."""
    if value is None or isinstance(value, (str, int, bool, dict, list)):
        return value
    return str(value)
