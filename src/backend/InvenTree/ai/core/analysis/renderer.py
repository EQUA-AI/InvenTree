"""Deterministic server rendering for evidence-analysis answers (S10).

The model never writes visible values: every number, date, identifier,
unit, and citation ordinal in a v2 answer is inserted here, from the
``EvidenceStore``, through a versioned template catalog whose literal text
contains no digits and no code-shaped tokens (pinned by unit test). The
closure checks (validator C04/C05) scan the rendered text against
``RenderedAnswer.inserted_values`` — which this module alone populates.

Citation ordinals are minted in first-citation order after claim order is
final; the ordinal manifest maps ``[n]`` markers to claims and source
coordinates (never Markdown character offsets).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from ai.core.i18n_templates import (
    ANALYSIS_ABSTAIN,
    ANALYSIS_DOWNGRADE,
    ANALYSIS_PARTIAL_NOTICE,
    deterministic_template,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from ai.core.analysis.evidence import EvidenceStore
    from ai.core.analysis.schemas import AnalysisClaim, IncompleteReason

RENDER_TEMPLATE_VERSION = 1

_MARKER_RE = re.compile(r"\s*\[\d+\]")


class RenderError(RuntimeError):
    """A claim referenced a template or slot the store cannot satisfy.

    The executor converts this into an abstention — a claim that cannot be
    rendered deterministically is never paraphrased around.
    """


#: (slots, paraphrase, marker, locale) -> one rendered sentence/paragraph.
if TYPE_CHECKING:
    BuildFn = Callable[[Mapping[str, str], str, str, str], str]


def _clause(paraphrase: str) -> str:
    """A bounded model paraphrase as a subordinate clause, or nothing."""
    cleaned = paraphrase.strip().rstrip(".")
    return f" — {cleaned}" if cleaned else ""


@dataclass(frozen=True, slots=True)
class RenderTemplate:
    """One deterministic sentence shape plus its validator metadata."""

    key: str
    required_slots: tuple[str, ...]
    build: Any  # BuildFn; typed loosely so the dataclass stays slots-frozen
    paraphrase_slot: bool = False
    requires_complete_population: bool = False
    requires_controlled_source: bool = False
    categorical_kind: str | None = None  # "absence" | "count" | None
    calibrated: bool = False
    #: S7: the claim's ``group_row`` facts render as table rows (one claim,
    #: one table — budgets don't move). Slots resolve from CALCULATION refs
    #: only, and the build fn receives ``rows`` as a fifth argument. Rows
    #: display keys/counts, never fenced labels: an operator-authored name
    #: (which may carry digits) must not enter rendered text — chips and
    #: the expansion endpoint carry the human names.
    iterates_rows: bool = False


def _t_record_count(slots, paraphrase, marker, locale):
    return (
        f"{slots['count']} matching records were found in the current "
        f"analysis scope{_clause(paraphrase)}.{marker}"
    )


def _t_record_line(slots, paraphrase, marker, locale):
    return (
        f"Work order {slots['reference']} — board status {slots['board_status']}, "
        f"lifecycle {slots['lifecycle_status']}, created {slots['created_at']}"
        f"{_clause(paraphrase)}.{marker}"
    )


def _t_latest_record(slots, paraphrase, marker, locale):
    return (
        f"The most recent matching record is {slots['reference']}, dated "
        f"{slots['date']}{_clause(paraphrase)}.{marker}"
    )


def _t_manual_passage_fact(slots, paraphrase, marker, locale):
    return f"From {slots['document']} (revision {slots['revision']}){_clause(paraphrase)}.{marker}"


def _t_applicability(slots, paraphrase, marker, locale):
    return (
        f"The applicability of {slots['document']} to the selected equipment "
        f"has not been verified; the association is ingest metadata only.{marker}"
    )


def _t_coverage_limitation(slots, paraphrase, marker, locale):
    return (
        f"Coverage note: {slots['returned_count']} of {slots['population_count']} "
        f"records were evaluated; conclusions are limited accordingly.{marker}"
    )


def _t_absence_of_records(slots, paraphrase, marker, locale):
    return (
        f"No matching records exist in the fully evaluated population of "
        f"{slots['population_count']} records.{marker}"
    )


def _t_no_relevant_passage(slots, paraphrase, marker, locale):
    return (
        "No relevant passage was retrieved from the searched sources for "
        f"this question; that is a retrieval outcome, not proof the source "
        f"lacks the information.{marker}"
    )


def _t_source_availability(slots, paraphrase, marker, locale):
    return (
        f"{slots['count']} document sources are registered for the selected "
        f"scope{_clause(paraphrase)}.{marker}"
    )


def _t_inference_note(slots, paraphrase, marker, locale):
    cleaned = paraphrase.strip().rstrip(".")
    return (
        f"Based on the cited evidence, this may indicate {cleaned}; treat it "
        f"as an interpretation, not a documented fact.{marker}"
    )


def _t_aggregate_summary(slots, paraphrase, marker, locale):
    return (
        f"The complete population is {slots['population_count']} records by "
        f"{slots['date_field']} ({slots['timezone']}){_clause(paraphrase)}.{marker}"
    )


def _t_group_breakdown(slots, paraphrase, marker, locale, rows):
    lines = [
        f"Breakdown by {slots['grouping']} across {slots['total_group_count']} "
        f"groups, {slots['population_count']} records{_clause(paraphrase)}:{marker}"
    ]
    for row in rows:
        lines.append(f"- {row.get('key', '')}: {row.get('group_count', '')}")
    if slots.get("remainder_group_count") not in (None, "", "0"):
        lines.append(
            f"- plus {slots['remainder_count']} records across "
            f"{slots['remainder_group_count']} further groups"
        )
    return "\n".join(lines)


def _t_timeline_breakdown(slots, paraphrase, marker, locale, rows):
    lines = [
        f"Series by {slots['bucket']} over {slots['bucket_count']} buckets, "
        f"{slots['population_count']} records{_clause(paraphrase)}:{marker}"
    ]
    for row in rows:
        lines.append(f"- {row.get('bucket', '')}: {row.get('group_count', '')}")
    return "\n".join(lines)


def _t_interval_stats(slots, paraphrase, marker, locale, rows):
    lines = [
        f"Repeat intervals over {slots['population_count']} events{_clause(paraphrase)}:{marker}"
    ]
    for row in rows:
        lines.append(
            f"- {row.get('key', '')}: {row.get('event_count', '')} events, "
            f"median {row.get('median_days', '')} days between events"
        )
    return "\n".join(lines)


def _t_duration_stats(slots, paraphrase, marker, locale):
    return (
        f"Across {slots['qualifying_count']} fully timed work orders the "
        f"median duration is {slots['median_minutes']} minutes (mean "
        f"{slots['mean_minutes']}, range {slots['min_minutes']} to "
        f"{slots['max_minutes']}); {slots['excluded_missing_count']} lacked "
        f"timestamps and {slots['excluded_invalid_count']} were invalid, and "
        f"none of those were estimated.{marker}"
    )


def _t_population_note(slots, paraphrase, marker, locale):
    return (
        f"Note: {slots['unassigned_machine_count']} work orders have no "
        f"machine assigned; they are counted in totals but excluded from "
        f"every per-machine grouping, and no machine was inferred.{marker}"
    )


def _t_null_date_note(slots, paraphrase, marker, locale):
    return (
        f"Note: {slots['null_date_count']} records have no value for the "
        f"selected date field and are excluded from the series, not "
        f"estimated into it.{marker}"
    )


def _t_downgrade_limitation(slots, paraphrase, marker, locale):
    return f"{deterministic_template(ANALYSIS_DOWNGRADE, locale)}{marker}"


def _t_abstention(slots, paraphrase, marker, locale):
    return f"{deterministic_template(ANALYSIS_ABSTAIN, locale)}{marker}"


RENDER_TEMPLATES: dict[str, RenderTemplate] = {
    template.key: template
    for template in (
        RenderTemplate(
            key="analysis.record_count",
            required_slots=("count",),
            build=_t_record_count,
            paraphrase_slot=True,
            requires_complete_population=True,
            categorical_kind="count",
        ),
        RenderTemplate(
            key="analysis.record_line",
            required_slots=(
                "reference",
                "board_status",
                "lifecycle_status",
                "created_at",
            ),
            build=_t_record_line,
            paraphrase_slot=True,
        ),
        RenderTemplate(
            key="analysis.latest_record",
            required_slots=("reference", "date"),
            build=_t_latest_record,
            paraphrase_slot=True,
            requires_complete_population=True,
        ),
        RenderTemplate(
            key="analysis.manual_passage_fact",
            required_slots=("document", "revision"),
            build=_t_manual_passage_fact,
            paraphrase_slot=True,
            requires_controlled_source=True,
        ),
        RenderTemplate(
            key="analysis.applicability",
            required_slots=("document",),
            build=_t_applicability,
        ),
        RenderTemplate(
            key="analysis.coverage_limitation",
            required_slots=("returned_count", "population_count"),
            build=_t_coverage_limitation,
        ),
        RenderTemplate(
            key="analysis.absence_of_records",
            required_slots=("population_count",),
            build=_t_absence_of_records,
            requires_complete_population=True,
            categorical_kind="absence",
        ),
        RenderTemplate(
            key="analysis.no_relevant_passage",
            required_slots=(),
            build=_t_no_relevant_passage,
        ),
        RenderTemplate(
            key="analysis.source_availability",
            required_slots=("count",),
            build=_t_source_availability,
            paraphrase_slot=True,
            requires_complete_population=True,
            categorical_kind="count",
        ),
        RenderTemplate(
            key="analysis.inference_note",
            required_slots=(),
            build=_t_inference_note,
            paraphrase_slot=True,
            calibrated=True,
        ),
        RenderTemplate(
            key="analysis.aggregate_summary",
            required_slots=("population_count", "date_field", "timezone"),
            build=_t_aggregate_summary,
            paraphrase_slot=True,
            requires_complete_population=True,
            categorical_kind="count",
        ),
        RenderTemplate(
            key="analysis.group_breakdown",
            required_slots=(
                "grouping",
                "population_count",
                "total_group_count",
                "remainder_group_count",
                "remainder_count",
            ),
            build=_t_group_breakdown,
            paraphrase_slot=True,
            requires_complete_population=True,
            categorical_kind="count",
            iterates_rows=True,
        ),
        RenderTemplate(
            key="analysis.timeline_breakdown",
            required_slots=("bucket", "bucket_count", "population_count"),
            build=_t_timeline_breakdown,
            paraphrase_slot=True,
            requires_complete_population=True,
            categorical_kind="count",
            iterates_rows=True,
        ),
        RenderTemplate(
            key="analysis.interval_stats",
            required_slots=("population_count",),
            build=_t_interval_stats,
            paraphrase_slot=True,
            requires_complete_population=True,
            iterates_rows=True,
        ),
        RenderTemplate(
            key="analysis.duration_stats",
            required_slots=(
                "qualifying_count",
                "median_minutes",
                "mean_minutes",
                "min_minutes",
                "max_minutes",
                "excluded_missing_count",
                "excluded_invalid_count",
            ),
            build=_t_duration_stats,
            requires_complete_population=True,
        ),
        RenderTemplate(
            key="analysis.population_note",
            required_slots=("unassigned_machine_count",),
            build=_t_population_note,
        ),
        RenderTemplate(
            key="analysis.null_date_note",
            required_slots=("null_date_count",),
            build=_t_null_date_note,
        ),
        RenderTemplate(
            key="analysis.downgrade_limitation",
            required_slots=(),
            build=_t_downgrade_limitation,
        ),
        RenderTemplate(
            key="analysis.abstention",
            required_slots=(),
            build=_t_abstention,
        ),
    )
}


@dataclass(frozen=True, slots=True)
class CitationEntry:
    """One ordinal in the manifest, with the claims that cite it."""

    ordinal: int
    source_type: str
    source_id: str | None
    source_title: str | None
    source_revision: str | None
    source_class: str | None
    controlled: bool
    as_of: str
    locator: dict[str, Any] | None
    evidence_set_id: str | None
    calculation: str | None
    claim_ids: tuple[str, ...]

    def wire(self) -> dict[str, Any]:
        """The ``CitationManifestEntry`` wire shape (availability is live)."""
        return {
            "ordinal": self.ordinal,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "source_title": self.source_title,
            "source_revision": self.source_revision,
            "source_class": self.source_class,
            "controlled": self.controlled,
            "as_of": self.as_of,
            "available": True,
            "locator": self.locator,
            "applicability": None,
            "evidence_set_id": self.evidence_set_id,
            "calculation": self.calculation,
        }


@dataclass
class OrdinalManifest:
    """First-citation-order ordinals plus the claim -> ordinals join."""

    entries: list[CitationEntry] = field(default_factory=list)
    by_claim: dict[str, tuple[int, ...]] = field(default_factory=dict)

    def marker_for(self, claim_id: str) -> str:
        ordinals = self.by_claim.get(claim_id, ())
        return "".join(f" [{ordinal}]" for ordinal in ordinals)

    def wire(self) -> list[dict[str, Any]]:
        return [entry.wire() for entry in self.entries]


def _fact_title(values: Mapping[str, str]) -> str | None:
    for key in ("document", "title", "reference"):
        rendered = values.get(key)
        if rendered:
            return rendered
    return None


def assign_ordinals(
    claims: Sequence[AnalysisClaim],
    store: EvidenceStore,
    *,
    default_as_of: str = "",
) -> OrdinalManifest:
    """Mint ``[1]..[n]`` in first-citation order; dedupe by source coords."""
    manifest = OrdinalManifest()
    index: dict[tuple[str, str, str], int] = {}  # coords -> entry position

    def _cite(claim_id: str, key: tuple[str, str, str], build_entry) -> int:
        position = index.get(key)
        if position is None:
            entry = build_entry(len(manifest.entries) + 1)
            manifest.entries.append(entry)
            index[key] = position = len(manifest.entries) - 1
        else:
            existing = manifest.entries[position]
            if claim_id not in existing.claim_ids:
                manifest.entries[position] = replace(
                    existing, claim_ids=(*existing.claim_ids, claim_id)
                )
        return manifest.entries[position].ordinal

    for claim in claims:
        ordinals: list[int] = []
        for fact_ref in claim.fact_refs:
            fact = store.facts.get(fact_ref)
            if fact is None:
                continue
            key = ("fact", f"{fact.source_class}:{fact.source_id}", fact.source_revision)
            ordinals.append(
                _cite(
                    claim.claim_id,
                    key,
                    lambda ordinal, fact=fact, claim_id=claim.claim_id: CitationEntry(
                        ordinal=ordinal,
                        source_type=fact.source_class,
                        source_id=fact.source_id,
                        source_title=_fact_title(fact.rendered_values()),
                        source_revision=fact.source_revision or None,
                        source_class=fact.source_class,
                        controlled=fact.controlled,
                        as_of=fact.as_of,
                        locator=dict(fact.locator) or None,
                        evidence_set_id=None,
                        calculation=None,
                        claim_ids=(claim_id,),
                    ),
                )
            )
        set_handles = [
            store.calculations[ref].evidence_set_handle
            for ref in claim.calculation_output_refs
            if ref in store.calculations and store.calculations[ref].evidence_set_handle
        ]
        set_handles.extend(ref for ref in claim.evidence_refs if ref in store.evidence_sets)
        for handle in set_handles:
            pending = store.evidence_sets.get(handle)
            if pending is None:
                continue
            key = ("set", handle, "")
            operation = str(pending.calculation.get("operation") or "")
            result = str(pending.calculation.get("result") or "")
            calculation = f"{operation}: {result}".strip(": ") or None
            ordinals.append(
                _cite(
                    claim.claim_id,
                    key,
                    lambda ordinal, pending=pending, calculation=calculation, claim_id=claim.claim_id: (
                        CitationEntry(
                            ordinal=ordinal,
                            source_type=f"{pending.source_class}_population",
                            source_id=pending.handle,
                            source_title=None,
                            source_revision=pending.snapshot_hash or None,
                            source_class=pending.source_class,
                            controlled=False,
                            as_of=default_as_of,
                            locator={"field": "population"},
                            evidence_set_id=pending.handle,
                            calculation=calculation,
                            claim_ids=(claim_id,),
                        )
                    ),
                )
            )
        for ref in claim.evidence_refs:
            if not ref.startswith("ret_"):
                continue
            key = ("retrieval", ref, "")
            ordinals.append(
                _cite(
                    claim.claim_id,
                    key,
                    lambda ordinal, ref=ref, claim_id=claim.claim_id: CitationEntry(
                        ordinal=ordinal,
                        source_type="retrieval_coverage",
                        source_id=ref,
                        source_title=None,
                        source_revision=None,
                        source_class=None,
                        controlled=False,
                        as_of=default_as_of,
                        locator={"field": "coverage"},
                        evidence_set_id=None,
                        calculation=None,
                        claim_ids=(claim_id,),
                    ),
                )
            )
        # De-dup while preserving first-citation order for the marker.
        manifest.by_claim[claim.claim_id] = tuple(dict.fromkeys(ordinals))
    return manifest


@dataclass(frozen=True, slots=True)
class RenderedSegment:
    """One claim's rendered text span (token -> claim attribution)."""

    claim_id: str
    text: str


@dataclass(frozen=True, slots=True)
class RenderedAnswer:
    """The complete server rendering plus the closure ground truth."""

    detailed_response: str
    spoken_summary: str
    reasoning_summary: str
    segments: tuple[RenderedSegment, ...]
    inserted_values: frozenset[str]
    citation_manifest: list[dict[str, Any]]


_REASONING_SUMMARY = (
    "Assembled from retrieved records and server calculations within the "
    "active analysis scope; every value is server-inserted from cited "
    "evidence."
)


def _resolve_slots(
    claim: AnalysisClaim, template: RenderTemplate, store: EvidenceStore
) -> dict[str, str]:
    """Fill template slots from the claim's referenced facts/calculations.

    An iterating template resolves slots from its CALCULATION refs only —
    its many row facts would otherwise collapse into one first-wins cell.
    """
    available: dict[str, str] = {}
    if not template.iterates_rows:
        for ref in claim.fact_refs:
            fact = store.facts.get(ref)
            if fact is not None:
                for name, rendered in fact.rendered_values().items():
                    available.setdefault(name, rendered)
    for ref in claim.calculation_output_refs:
        calculation = store.calculations.get(ref)
        if calculation is not None:
            for name, rendered in calculation.rendered_values().items():
                available.setdefault(name, rendered)
    slots: dict[str, str] = {}
    for name in template.required_slots:
        if name not in available:
            raise RenderError(
                f"claim {claim.claim_id}: template {template.key} slot '{name}' "
                "has no referenced value"
            )
        slots[name] = available[name]
    return slots


def _resolve_rows(claim: AnalysisClaim, store: EvidenceStore) -> list[dict[str, str]]:
    """The claim's ``group_row`` facts as rendered rows, in reference order.

    Every cell string joins ``inserted_values``, which is what closes C05
    over a whole table by construction: a rendered cell can only ever be a
    server-rendered fact value.
    """
    rows: list[dict[str, str]] = []
    for ref in claim.fact_refs:
        fact = store.facts.get(ref)
        if fact is not None and fact.kind == "group_row":
            rows.append(fact.rendered_values())
    return rows


def render_answer(
    claims: Sequence[AnalysisClaim],
    store: EvidenceStore,
    *,
    ordinals: OrdinalManifest,
    locale: str | None = None,
    state: str = "complete",
    incomplete_reasons: Sequence[IncompleteReason] = (),
) -> RenderedAnswer:
    """Render every claim deterministically; collect the closure index."""
    segments: list[RenderedSegment] = []
    inserted: set[str] = set()
    for claim in claims:
        template = RENDER_TEMPLATES.get(claim.render_template)
        if template is None:
            raise RenderError(
                f"claim {claim.claim_id}: unknown render template '{claim.render_template}'"
            )
        slots = _resolve_slots(claim, template, store)
        paraphrase = claim.paraphrase if template.paraphrase_slot else ""
        marker = ordinals.marker_for(claim.claim_id)
        if template.iterates_rows:
            rows = _resolve_rows(claim, store)
            text = template.build(slots, paraphrase, marker, locale or "en", rows)
            for row in rows:
                inserted.update(row.values())
        else:
            text = template.build(slots, paraphrase, marker, locale or "en")
        segments.append(RenderedSegment(claim_id=claim.claim_id, text=text))
        inserted.update(slots.values())
        for ordinal in ordinals.by_claim.get(claim.claim_id, ()):
            inserted.add(f"[{ordinal}]")

    parts = [segment.text for segment in segments]
    if state == "partial":
        notice = deterministic_template(ANALYSIS_PARTIAL_NOTICE, locale)
        facets = sorted({reason.facet for reason in incomplete_reasons})
        if facets:
            notice = f"{notice} (unfinished: {', '.join(facets)})"
        parts.append(notice)
    detailed_response = (
        "\n\n".join(parts) if parts else deterministic_template(ANALYSIS_ABSTAIN, locale)
    )

    spoken_summary = ""
    if state == "complete":
        # Iterating tables are unreadable as speech; the summary claim
        # speaks for them.
        answer_segments = [
            segment
            for claim, segment in zip(claims, segments, strict=True)
            if claim.claim_role == "answer"
            and not getattr(RENDER_TEMPLATES.get(claim.render_template), "iterates_rows", False)
        ]
        spoken_parts = [_MARKER_RE.sub("", segment.text).strip() for segment in answer_segments[:2]]
        spoken_summary = " ".join(part for part in spoken_parts if part)

    return RenderedAnswer(
        detailed_response=detailed_response,
        spoken_summary=spoken_summary,
        reasoning_summary=_REASONING_SUMMARY,
        segments=tuple(segments),
        inserted_values=frozenset(inserted),
        citation_manifest=ordinals.wire(),
    )


__all__ = [
    "RENDER_TEMPLATES",
    "RENDER_TEMPLATE_VERSION",
    "CitationEntry",
    "OrdinalManifest",
    "RenderError",
    "RenderTemplate",
    "RenderedAnswer",
    "RenderedSegment",
    "assign_ordinals",
    "render_answer",
]
