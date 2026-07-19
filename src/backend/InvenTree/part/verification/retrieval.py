"""Bounded, tiered, deterministic candidate retrieval.

Retrieval finds possibilities; it never proves compatibility. Every tier is
explicit, capped, deduplicated by base Part, and recorded as a retrieval
reason. Whether the universe was complete (no cap hit) is reported honestly
so an exhaustive no-safe-match claim can be blocked (spec section 9.1).
"""

from dataclasses import dataclass, field

from django.db.models import Q


@dataclass
class CandidateEntry:
    """One deduplicated candidate with all of its retrieval reasons."""

    part: object
    tiers: list[str] = field(default_factory=list)


@dataclass
class RetrievalResult:
    """Deterministically ordered retrieval output."""

    entries: list[CandidateEntry] = field(default_factory=list)
    universe_complete: bool = True
    total_considered: int = 0


# Tier identifiers in deterministic evaluation order (spec section 9.1)
TIER_ORDER = [
    'requested',
    'bom_component',
    'bom_substitute',
    'bom_variant',
    'mpn',
    'sku',
    'ipn',
    'revision',
    'related',
]


def _tier_requested(session):
    """Tier 1: the exact requested part."""
    return [session.requested_part] if session.requested_part else []


def _tier_bom_component(session):
    """Tier 2: the exact current BOM line component."""
    if session.bom_item is None:
        return []
    return [session.bom_item.sub_part]


def _tier_bom_substitute(session):
    """Tier 3: explicit substitutes for the exact BOM line only."""
    if session.bom_item is None:
        return []
    return [row.part for row in session.bom_item.substitutes.select_related('part')]


def _tier_bom_variant(session):
    """Tier 4: variants allowed for that BOM context only."""
    if session.bom_item is None or not session.bom_item.allow_variants:
        return []
    return list(session.bom_item.sub_part.get_descendants(include_self=False))


def _tier_mpn(session):
    """Tier 5: exact manufacturer + normalized MPN matches.

    The same MPN under a different manufacturer remains a distinct candidate;
    retrieval never collapses manufacturer namespaces (spec section 9.2).
    """
    from company.models import ManufacturerPart

    part = session.requested_part
    if part is None:
        return []

    results = []
    for mp in part.manufacturer_parts.all():
        if not mp.MPN:
            continue
        rows = ManufacturerPart.objects.filter(
            MPN__iexact=mp.MPN.strip()
        ).select_related('part')
        results.extend(row.part for row in rows)
    return results


def _tier_sku(session):
    """Tier 6: exact supplier + SKU mapped to a base part."""
    from company.models import SupplierPart

    part = session.requested_part
    if part is None:
        return []

    results = []
    for sp in part.supplier_parts.all():
        rows = SupplierPart.objects.filter(
            supplier=sp.supplier, SKU__iexact=sp.SKU.strip()
        ).select_related('part')
        results.extend(row.part for row in rows)
    return results


def _tier_ipn(session):
    """Tier 7: unambiguous normalized IPN matches."""
    from part.models import Part

    part = session.requested_part
    if part is None or not part.IPN:
        return []

    return list(Part.objects.filter(IPN__iexact=part.IPN.strip()))


def _tier_revision(session):
    """Tier 8: revision relations with explicit policy meaning."""
    part = session.requested_part
    if part is None:
        return []

    results = []
    if part.revision_of is not None:
        results.append(part.revision_of)
    results.extend(part.revisions.all())
    return results


def _tier_related(session):
    """Tier 9: generic related parts as retrieval-only context."""
    from part.models import PartRelated

    part = session.requested_part
    if part is None:
        return []

    results = []
    rows = PartRelated.objects.filter(Q(part_1=part) | Q(part_2=part)).select_related(
        'part_1', 'part_2'
    )
    for row in rows:
        results.append(row.part_2 if row.part_1_id == part.pk else row.part_1)
    return results


_TIER_FUNCTIONS = {
    'requested': _tier_requested,
    'bom_component': _tier_bom_component,
    'bom_substitute': _tier_bom_substitute,
    'bom_variant': _tier_bom_variant,
    'mpn': _tier_mpn,
    'sku': _tier_sku,
    'ipn': _tier_ipn,
    'revision': _tier_revision,
    'related': _tier_related,
}


def retrieve_candidates(
    session, *, max_candidates: int, tier_cap: int
) -> RetrievalResult:
    """Run all retrieval tiers with caps and deterministic order.

    Inactive parts are excluded from the candidate universe. Candidates are
    deduplicated by base Part primary key while every retrieval reason is
    retained; order is tier order then primary key.
    """
    result = RetrievalResult()
    seen: dict[int, CandidateEntry] = {}

    for tier in TIER_ORDER:
        parts = _TIER_FUNCTIONS[tier](session)

        active_parts = sorted(
            (part for part in parts if part is not None and part.active),
            key=lambda part: part.pk,
        )

        if len(active_parts) > tier_cap:
            active_parts = active_parts[:tier_cap]
            result.universe_complete = False

        for part in active_parts:
            entry = seen.get(part.pk)
            if entry is None:
                if len(seen) >= max_candidates:
                    result.universe_complete = False
                    break
                entry = CandidateEntry(part=part)
                seen[part.pk] = entry
                result.entries.append(entry)
            if tier not in entry.tiers:
                entry.tiers.append(tier)

    result.total_considered = len(result.entries)
    return result
