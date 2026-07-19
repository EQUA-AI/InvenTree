"""Deterministic hard-compatibility evaluation.

Every hard rule is evaluated before any ranking; an excluded candidate never
receives a competitive rank and no score, availability, or preference can
restore it (spec sections 9.3 and RPF-ADR-002/007).

Missing candidate proof is never a wildcard: policy chooses between exclusion
and indeterminate handling, and neither is a pass.
"""

from dataclasses import dataclass, field
from decimal import Decimal

from part.verification.normalization import NormalizationError, canonical_value
from part.verification.schema import (
    BlockerCodes,
    HardResult,
    RequirementResolution,
    RequirementValueKind,
    canonical_json,
)
from part.verification.sources import SourceFact, parameter_facts


@dataclass(frozen=True)
class AttributeResult:
    """Result of one hard-rule evaluation for one candidate attribute."""

    key: str
    outcome: str
    reason_code: str
    requirement_display: dict
    candidate_display: dict
    evidence_refs: tuple = ()
    remediation: str = ''

    def as_dict(self) -> dict:
        """Return the canonical projection of this result."""
        return {
            'key': self.key,
            'outcome': self.outcome,
            'reason_code': self.reason_code,
            'requirement': self.requirement_display,
            'candidate': self.candidate_display,
            'evidence_refs': list(self.evidence_refs),
            'remediation': self.remediation,
        }


@dataclass
class CandidateResult:
    """Complete deterministic evaluation of one candidate."""

    eligible: bool = False
    hard_conflicts: list = field(default_factory=list)
    matched_attributes: list = field(default_factory=list)
    missing_attributes: list = field(default_factory=list)
    soft_matched: int = 0
    soft_considered: int = 0
    candidate_fact_count: int = 0


def _tolerance_bounds(value: Decimal, tolerance: dict) -> tuple[Decimal, Decimal]:
    """Return the inclusive comparison bounds for a decimal with tolerance."""
    kind = (tolerance or {}).get('kind', 'absolute')
    magnitude = Decimal(str((tolerance or {}).get('value', '0')))
    delta = abs(value) * magnitude / Decimal(100) if kind == 'percent' else magnitude
    return value - delta, value + delta


def _as_range(value) -> tuple[Decimal | None, Decimal | None]:
    """Coerce a canonical decimal or range value into decimal bounds."""
    if isinstance(value, dict):
        low = Decimal(value['min']) if value.get('min') is not None else None
        high = Decimal(value['max']) if value.get('max') is not None else None
        return low, high
    scalar = Decimal(str(value))
    return scalar, scalar


def compare_values(operator: str, kind: str, requirement, candidate, tolerance) -> bool:
    """Compare canonical requirement and candidate values under one operator.

    Semantics (spec sections 7.3 and 9.3): ``range_within`` means the
    candidate value or range fits inside the application envelope;
    ``range_contains`` means the candidate rating covers the application
    envelope; ``gte``/``lte`` compare the candidate rating against the
    required floor/ceiling.
    """
    if operator == 'present':
        return candidate is not None

    if operator == 'eq':
        if kind == RequirementValueKind.DECIMAL:
            low, high = _tolerance_bounds(Decimal(str(requirement)), tolerance)
            return low <= Decimal(str(candidate)) <= high
        return requirement == candidate

    if operator == 'gte':
        return Decimal(str(candidate)) >= Decimal(str(requirement))

    if operator == 'lte':
        return Decimal(str(candidate)) <= Decimal(str(requirement))

    if operator == 'range_within':
        req_low, req_high = _as_range(requirement)
        cand_low, cand_high = _as_range(candidate)
        low_ok = req_low is None or (cand_low is not None and cand_low >= req_low)
        high_ok = req_high is None or (cand_high is not None and cand_high <= req_high)
        return low_ok and high_ok

    if operator == 'range_contains':
        req_low, req_high = _as_range(requirement)
        cand_low, cand_high = _as_range(candidate)
        low_ok = cand_low is None or (req_low is not None and cand_low <= req_low)
        high_ok = cand_high is None or (req_high is not None and cand_high >= req_high)
        return low_ok and high_ok

    if operator == 'in':
        members = requirement if isinstance(requirement, list) else [requirement]
        return candidate in members

    if operator == 'contains':
        required = requirement if isinstance(requirement, list) else [requirement]
        held = candidate if isinstance(candidate, list) else [candidate]
        return all(member in held for member in required)

    if operator == 'compatible_revision':
        if isinstance(requirement, dict):
            allowed = requirement.get('allowed', [])
            return candidate in allowed
        return candidate == requirement

    return False


def _candidate_field_fact(candidate, field_name: str, key: str) -> list[SourceFact]:
    """Read one allowlisted candidate identity field as a fact."""
    value = getattr(candidate, field_name, None)
    if value in (None, ''):
        return []
    return [
        SourceFact(
            key=key,
            raw_value=value,
            unit='',
            authority=f'candidate:{field_name}',
            source_kind='catalog',
            source_model='part.part',
            source_object_id=str(candidate.pk),
            source_field=field_name,
        )
    ]


def candidate_facts(candidate, spec: dict) -> list[SourceFact]:
    """Gather candidate-side facts for one policy requirement.

    Uses the policy's explicit ``candidate_sources`` when declared; otherwise
    falls back to the parameter templates named by the requirement's own
    sources, so requirement and candidate read the same typed mapping.
    """
    key = spec['key']
    sources = spec.get('candidate_sources')

    if not sources:
        sources = [
            {'kind': 'parameter', 'template': source['template']}
            for source in spec.get('sources', [])
            if source.get('kind') == 'parameter'
        ]

    facts: list[SourceFact] = []
    for source in sources:
        if source['kind'] == 'parameter':
            facts.extend(parameter_facts(candidate, source['template'], key))
        elif source['kind'] == 'field':
            facts.extend(_candidate_field_fact(candidate, source['field'], key))
    return facts


def _canonical_candidate_value(spec: dict, fact: SourceFact):
    """Canonicalize one candidate fact for comparison."""
    raw = fact.raw_value
    kind = spec['value_kind']
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


def evaluate_candidate(
    candidate, specs: list[dict], requirements_by_key: dict
) -> CandidateResult:
    """Evaluate every applicable rule for one candidate.

    ``requirements_by_key`` maps requirement key to the resolved requirement
    dict from requirement construction. The candidate is eligible exactly when
    every required hard result is PASS and the candidate identity floors pass.
    """
    result = CandidateResult()
    results: list[AttributeResult] = []

    # Candidate identity floor: inactive parts are never eligible
    if not candidate.active:
        results.append(
            AttributeResult(
                key='identity.active',
                outcome=HardResult.CONFLICT,
                reason_code=BlockerCodes.PART_INACTIVE,
                requirement_display={'value': True},
                candidate_display={'value': False},
            )
        )

    for spec in specs:
        key = spec['key']
        requirement = requirements_by_key.get(key)
        hard = spec.get('hard', True)

        if requirement is None:
            continue

        requirement_display = {
            'value': requirement['value'],
            'unit': requirement['unit'],
            'operator': requirement['operator'],
            'tolerance': requirement['tolerance'],
        }

        if requirement['resolution'] != RequirementResolution.ACCEPTED:
            # Hard requirement-side gaps block evaluation upstream; soft
            # unresolved requirements are simply not comparable.
            if not hard:
                continue
            results.append(
                AttributeResult(
                    key=key,
                    outcome=HardResult.INDETERMINATE,
                    reason_code=requirement['blocker_code']
                    or BlockerCodes.REQUIRED_ATTRIBUTE_MISSING,
                    requirement_display=requirement_display,
                    candidate_display={},
                )
            )
            continue

        facts = candidate_facts(candidate, spec)
        result.candidate_fact_count += 1 if facts else 0

        if not facts:
            behavior = spec.get('candidate_missing', 'exclude')
            outcome = (
                HardResult.MISSING
                if behavior == 'exclude'
                else HardResult.INDETERMINATE
            )
            entry = AttributeResult(
                key=key,
                outcome=outcome if hard else HardResult.MISSING,
                reason_code=BlockerCodes.CANDIDATE_ATTRIBUTE_MISSING,
                requirement_display=requirement_display,
                candidate_display={},
                remediation='Record the missing candidate attribute in the catalog',
            )
            if hard:
                results.append(entry)
            else:
                result.soft_considered += 1
            continue

        try:
            canonical_values = [
                _canonical_candidate_value(spec, fact) for fact in facts
            ]
        except NormalizationError as error:
            if hard:
                results.append(
                    AttributeResult(
                        key=key,
                        outcome=HardResult.INDETERMINATE,
                        reason_code=error.code,
                        requirement_display=requirement_display,
                        candidate_display={'raw': str(facts[0].raw_value)},
                    )
                )
            else:
                result.soft_considered += 1
            continue

        # Contradictory candidate facts from equal-authority sources block
        # (spec section 8.3): a matching value never masks a conflicting one.
        if len({canonical_json(value) for value in canonical_values}) > 1:
            if hard:
                results.append(
                    AttributeResult(
                        key=key,
                        outcome=HardResult.INDETERMINATE,
                        reason_code=BlockerCodes.EVIDENCE_CONFLICT,
                        requirement_display=requirement_display,
                        candidate_display={
                            'values': canonical_values,
                            'raw': str(facts[0].raw_value),
                        },
                        remediation='Resolve the contradictory candidate facts',
                    )
                )
            else:
                result.soft_considered += 1
            continue

        matched = any(
            compare_values(
                requirement['operator'],
                requirement['value_kind'],
                requirement['value'],
                value,
                requirement['tolerance'],
            )
            for value in canonical_values
        )

        candidate_display = {
            'value': canonical_values[0],
            'raw': str(facts[0].raw_value),
            'unit': requirement['unit'],
        }
        evidence_refs = tuple(
            f'{fact.source_model}:{fact.source_object_id}' for fact in facts
        )

        if not hard:
            result.soft_considered += 1
            if matched:
                result.soft_matched += 1
            continue

        if matched:
            results.append(
                AttributeResult(
                    key=key,
                    outcome=HardResult.PASS,
                    reason_code='',
                    requirement_display=requirement_display,
                    candidate_display=candidate_display,
                    evidence_refs=evidence_refs,
                )
            )
        else:
            results.append(
                AttributeResult(
                    key=key,
                    outcome=HardResult.CONFLICT,
                    reason_code=spec.get('conflict_code')
                    or BlockerCodes.ATTRIBUTE_CONFLICT,
                    requirement_display=requirement_display,
                    candidate_display=candidate_display,
                    evidence_refs=evidence_refs,
                )
            )

    for entry in results:
        record = entry.as_dict()
        if entry.outcome == HardResult.PASS:
            result.matched_attributes.append(record)
        elif entry.outcome == HardResult.CONFLICT:
            result.hard_conflicts.append(record)
        else:
            result.missing_attributes.append(record)

    result.eligible = not result.hard_conflicts and not result.missing_attributes
    return result
