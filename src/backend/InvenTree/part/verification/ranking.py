"""Visible, deterministic ranking for eligible survivors only.

Rank factors are policy-versioned, individually explainable, and never award
points for stock, price, lead time, supplier preference, or AI confidence
(spec section 10.1). Ranking cannot alter eligibility.
"""

from dataclasses import dataclass, field


@dataclass
class RankInput:
    """Inputs the rank factors may consume for one eligible candidate."""

    candidate: object
    tiers: list[str] = field(default_factory=list)
    soft_matched: int = 0
    soft_considered: int = 0
    candidate_fact_count: int = 0
    policy_attribute_count: int = 0
    evidence_has_expiry: bool = False
    session = None


# Relation tiers that constitute an exact application relation
_APPLICATION_TIERS = frozenset({'bom_component', 'bom_substitute', 'bom_variant'})


def _factor_exact_requested_identity(inputs: RankInput, maximum: int):
    """Full credit when the candidate is the exact requested part."""
    hit = inputs.session.requested_part_id == inputs.candidate.pk
    return maximum if hit else 0, 'exact requested part' if hit else 'different part'


def _factor_exact_application_relation(inputs: RankInput, maximum: int):
    """Full credit for an exact line substitute or allowed variant."""
    hit = bool(_APPLICATION_TIERS.intersection(inputs.tiers))
    return maximum if hit else 0, (
        'exact BOM application relation' if hit else 'no exact application relation'
    )


def _factor_revision_preference(inputs: RankInput, maximum: int):
    """Full credit when the candidate revision equals the requested revision."""
    requested = inputs.session.requested_part
    if requested is None:
        return 0, 'no requested revision context'
    hit = (inputs.candidate.revision or '') == (requested.revision or '')
    return maximum if hit else 0, 'revision match' if hit else 'revision differs'


def _factor_evidence_coverage(inputs: RankInput, maximum: int):
    """Proportional credit for ranked (soft) facts that matched."""
    if inputs.soft_considered == 0:
        return 0, 'no ranked attributes declared'
    contribution = (maximum * inputs.soft_matched) // inputs.soft_considered
    return contribution, (
        f'{inputs.soft_matched}/{inputs.soft_considered} ranked attributes matched'
    )


def _factor_asset_history_relevance(inputs: RankInput, maximum: int):
    """Full credit when the candidate is installed on the exact asset."""
    machine = inputs.session.machine
    if machine is None:
        return 0, 'no asset context'
    hit = machine.machine_parts.filter(part=inputs.candidate).exists()
    return maximum if hit else 0, (
        'installed on this asset' if hit else 'not installed on this asset'
    )


def _factor_catalog_completeness(inputs: RankInput, maximum: int):
    """Proportional credit for candidate attributes present in the catalog."""
    if inputs.policy_attribute_count == 0:
        return 0, 'no policy attributes'
    contribution = (
        maximum * inputs.candidate_fact_count
    ) // inputs.policy_attribute_count
    return contribution, (
        f'{inputs.candidate_fact_count}/{inputs.policy_attribute_count} '
        'attributes recorded'
    )


def _factor_preferred_representation(inputs: RankInput, maximum: int):
    """Full credit for the approved base catalog representation."""
    hit = inputs.candidate.variant_of_id is None
    return maximum if hit else 0, (
        'base representation' if hit else 'variant representation'
    )


def _factor_freshness(inputs: RankInput, maximum: int):
    """Full credit when no consumed evidence carries an expiry."""
    hit = not inputs.evidence_has_expiry
    return maximum if hit else 0, (
        'no expiring evidence' if hit else 'expiring evidence in use'
    )


_FACTOR_FUNCTIONS = {
    'exact_requested_identity': _factor_exact_requested_identity,
    'exact_application_relation': _factor_exact_application_relation,
    'revision_preference': _factor_revision_preference,
    'evidence_coverage': _factor_evidence_coverage,
    'asset_history_relevance': _factor_asset_history_relevance,
    'catalog_completeness': _factor_catalog_completeness,
    'preferred_representation': _factor_preferred_representation,
    'freshness': _factor_freshness,
}


def compute_rank_factors(inputs: RankInput, factor_weights: list[dict]):
    """Compute all visible factors and the total rank value for one survivor.

    Returns ``(factors, rank_value)`` where each factor exposes its id, input
    reason, contribution, and maximum (FR-RPF-023).
    """
    factors = []
    total = 0

    for weight in factor_weights:
        function = _FACTOR_FUNCTIONS[weight['id']]
        contribution, reason = function(inputs, weight['max'])
        factors.append({
            'id': weight['id'],
            'contribution': contribution,
            'max': weight['max'],
            'reason': reason,
        })
        total += contribution

    return factors, total


def survivor_sort_key(entry: dict):
    """Deterministic comparison-order key for eligible survivors.

    Tie order (spec section 10.1): rank value, exact requested identity,
    exact application relation, evidence coverage, freshness, then Part pk.
    """

    def factor(entry_id):
        for item in entry['rank_factors']:
            if item['id'] == entry_id:
                return item['contribution']
        return 0

    return (
        -entry['rank_value'],
        -factor('exact_requested_identity'),
        -factor('exact_application_relation'),
        -factor('evidence_coverage'),
        -factor('freshness'),
        entry['candidate_pk'],
    )
