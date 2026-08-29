"""The typed fact/calculation store the analysis executor accumulates (S10).

Everything a v2 answer may state as a value lives here first, as a
``FactValue`` with one deterministic ``render()``. The renderer inserts
those renderings into template slots; the validator's closure checks then
scan every visible token against ``inserted_value_index()`` — a number,
date, or identifier that never passed through this store cannot survive.

``synthesis_view()`` is the fence toward the model: facts and calculation
results are visible (the model organizes them into claims), but evidence
sets appear as opaque digests — never member rows — and
``authorization_scope_hash`` never enters the view (it stays in the
capture ledger and the DB column, per ``ai.core.contracts.retrieval``).

Values of untrusted origin (titles, narrative fields) must arrive here
ALREADY fenced by the reader/corpus projections (the S5b contract);
adapters pass them through verbatim and never unfence.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping, Sequence

#: §7.6: the adopted tenant design envelope. Larger exact-audit calculations
#: abstain until a scalable snapshot store is approved.
EVIDENCE_SET_MEMBER_CAP = 25_000

ValueType = Literal[
    "int",
    "decimal",
    "date",
    "datetime",
    "duration_days",
    "identifier",
    "enum",
    "unit_quantity",
    "bool",
    "text",
]

FactKind = Literal[
    "record_field",
    "coverage",
    "source_state",
    "manual_passage",
    "inventory_entry",
    # S7 complete-population analytics:
    "maintenance_record",
    "group_row",
    "dataset_profile",
    # S8b verified applicability (C07 extension):
    "applicability",
]

CalculationOperation = Literal[
    "count",
    "latest",
    "min",
    "max",
    # S7 complete-population analytics:
    "sum",
    "mean",
    "median",
    "group_count",
    "bucket_count",
    "interval_stats",
    "duration_stats",
    # S9 comparison: the deterministic per-status tally.
    "comparison_statuses",
]


@dataclass(frozen=True, slots=True)
class FactValue:
    """One typed value with the single deterministic rendering."""

    value_type: ValueType
    raw: Any
    unit: str | None = None

    def render(self) -> str:
        """The ONLY place a stored value becomes visible text."""
        if self.raw is None:
            return "not recorded"
        if self.value_type == "bool":
            return "yes" if self.raw else "no"
        if self.value_type == "int":
            return str(int(self.raw))
        if self.value_type == "decimal":
            text = f"{self.raw}"
            return text.rstrip("0").rstrip(".") if "." in text else text
        if self.value_type == "duration_days":
            count = int(self.raw)
            return "1 day" if count == 1 else f"{count} days"
        if self.value_type == "unit_quantity":
            rendered = f"{self.raw}"
            return f"{rendered} {self.unit}" if self.unit else rendered
        if self.value_type in ("date", "datetime"):
            # Adapters store ISO strings (readers project via _iso); a
            # datetime object renders through isoformat for determinism.
            return self.raw if isinstance(self.raw, str) else self.raw.isoformat()
        # identifier / enum / text: verbatim (fenced upstream when untrusted).
        return str(self.raw)


@dataclass(frozen=True, slots=True)
class TypedFact:
    """One retrieved, authorization-checked fact with typed values."""

    fact_id: str
    kind: FactKind
    source_class: str
    source_id: str
    source_revision: str
    locator: dict[str, Any]
    retrieval_id: str
    as_of: str
    authorization_class: str
    values: Mapping[str, FactValue]
    entity_refs: tuple[str, ...] = ()
    machine_id: int | None = None
    controlled: bool = False

    def rendered_values(self) -> dict[str, str]:
        return {name: value.render() for name, value in self.values.items()}


@dataclass(frozen=True, slots=True)
class CalculationOutput:
    """A completed, typed server calculation (Tier-1 vocabulary)."""

    calculation_id: str
    operation: CalculationOperation
    input_refs: tuple[str, ...]
    evidence_set_handle: str | None
    complete_population: bool
    values: Mapping[str, FactValue]

    def rendered_values(self) -> dict[str, str]:
        return {name: value.render() for name, value in self.values.items()}


def mint_evidence_set_handle() -> str:
    """The opaque handle the model sees; becomes the DB pk on persist."""
    return f"set_{uuid.uuid4().hex}"


@dataclass
class PendingEvidenceSet:
    """The in-memory twin of a future ``ChatEvidenceSet`` row (§7.6)."""

    handle: str
    source_class: str
    filters: dict[str, Any]
    population_count: int
    evaluated_count: int
    complete_population: bool
    displayed_count: int = 0
    high_watermarks: dict[str, Any] = field(default_factory=dict)
    snapshot_hash: str = ""
    supports_expansion: bool = True
    member_cap: int = EVIDENCE_SET_MEMBER_CAP
    calculation: dict[str, Any] = field(default_factory=dict)
    #: (member_index, source_class, source_object_id, source_version)
    members: list[tuple[int, str, str, str]] = field(default_factory=list)

    def add_member(
        self, source_class: str, source_object_id: str, source_version: str = ""
    ) -> bool:
        """Append one evaluated operand; ``False`` past the cap.

        Past-cap sets degrade to digest-only (``supports_expansion`` drops)
        and any exact-audit claim over them must abstain — the caller's
        responsibility, checked by the validator's population rules.
        """
        if len(self.members) >= self.member_cap:
            self.supports_expansion = False
            return False
        self.members.append((
            len(self.members) + 1,
            source_class,
            str(source_object_id),
            source_version,
        ))
        return True

    def digest(self) -> dict[str, Any]:
        """The ONLY shape the model may see for this set: no members."""
        return {
            "handle": self.handle,
            "source_class": self.source_class,
            "operation": self.calculation.get("operation", ""),
            "result": self.calculation.get("result", ""),
            "population_count": self.population_count,
            "complete_population": self.complete_population,
        }


@dataclass
class EvidenceStore:
    """Everything one analysis turn retrieved, typed and indexed."""

    facts: dict[str, TypedFact] = field(default_factory=dict)
    calculations: dict[str, CalculationOutput] = field(default_factory=dict)
    evidence_sets: dict[str, PendingEvidenceSet] = field(default_factory=dict)
    #: Model-visible retrieval envelopes (§7.4), as recorded by the tools.
    envelopes: list[dict[str, Any]] = field(default_factory=list)
    _primary_coverage: dict[str, Any] | None = None
    _fact_seq: int = 0
    _calc_seq: int = 0

    # -- accumulation ------------------------------------------------------

    def add_fact(
        self,
        *,
        kind: FactKind,
        source_class: str,
        source_id: str,
        source_revision: str,
        locator: dict[str, Any],
        retrieval_id: str,
        as_of: str,
        authorization_class: str,
        values: Mapping[str, FactValue],
        entity_refs: Sequence[str] = (),
        machine_id: int | None = None,
        controlled: bool = False,
    ) -> str:
        self._fact_seq += 1
        fact_id = f"fact_{self._fact_seq}"
        self.facts[fact_id] = TypedFact(
            fact_id=fact_id,
            kind=kind,
            source_class=source_class,
            source_id=str(source_id),
            source_revision=str(source_revision),
            locator=dict(locator),
            retrieval_id=retrieval_id,
            as_of=as_of,
            authorization_class=authorization_class,
            values=dict(values),
            entity_refs=tuple(entity_refs),
            machine_id=machine_id,
            controlled=controlled,
        )
        return fact_id

    def add_calculation(
        self,
        *,
        operation: CalculationOperation,
        input_refs: Sequence[str],
        values: Mapping[str, FactValue],
        evidence_set_handle: str | None = None,
        complete_population: bool = False,
    ) -> str:
        self._calc_seq += 1
        calculation_id = f"calc_{self._calc_seq}"
        self.calculations[calculation_id] = CalculationOutput(
            calculation_id=calculation_id,
            operation=operation,
            input_refs=tuple(input_refs),
            evidence_set_handle=evidence_set_handle,
            complete_population=complete_population,
            values=dict(values),
        )
        return calculation_id

    def open_evidence_set(
        self,
        *,
        source_class: str,
        filters: Mapping[str, Any],
        population_count: int,
        evaluated_count: int,
        complete_population: bool,
        snapshot_hash: str = "",
        high_watermarks: Mapping[str, Any] | None = None,
        calculation: Mapping[str, Any] | None = None,
    ) -> PendingEvidenceSet:
        pending = PendingEvidenceSet(
            handle=mint_evidence_set_handle(),
            source_class=source_class,
            filters=dict(filters),
            population_count=population_count,
            evaluated_count=evaluated_count,
            complete_population=complete_population,
            snapshot_hash=snapshot_hash,
            high_watermarks=dict(high_watermarks or {}),
            calculation=dict(calculation or {}),
        )
        self.evidence_sets[pending.handle] = pending
        return pending

    def record_envelope(self, envelope: Mapping[str, Any]) -> None:
        """Keep the model-visible envelope half for ledger/contradiction checks."""
        self.envelopes.append(dict(envelope))

    def set_primary_coverage(self, coverage: Mapping[str, Any]) -> None:
        """The coverage block the wire attachment and copy/export surface."""
        self._primary_coverage = dict(coverage)

    # -- derived views -----------------------------------------------------

    def coverage_meta(self) -> dict[str, Any] | None:
        return dict(self._primary_coverage) if self._primary_coverage else None

    def inserted_value_index(self) -> frozenset[str]:
        """Every rendering a template slot may legitimately contain."""
        rendered: set[str] = set()
        for fact in self.facts.values():
            rendered.update(fact.rendered_values().values())
        for calculation in self.calculations.values():
            rendered.update(calculation.rendered_values().values())
        return frozenset(rendered)

    def retrieval_ids(self) -> frozenset[str]:
        ids = {fact.retrieval_id for fact in self.facts.values()}
        ids.update(str(envelope.get("retrieval_id", "")) for envelope in self.envelopes)
        ids.discard("")
        return frozenset(ids)

    def synthesis_view(self, *, template_keys: Sequence[str] = ()) -> dict[str, Any]:
        """The fenced view the synthesis model receives. Digests only."""
        return {
            "facts": [
                {
                    "fact_id": fact.fact_id,
                    "kind": fact.kind,
                    "source_class": fact.source_class,
                    "source_id": fact.source_id,
                    "source_revision": fact.source_revision,
                    "as_of": fact.as_of,
                    "controlled": fact.controlled,
                    "entity_refs": list(fact.entity_refs),
                    "values": fact.rendered_values(),
                }
                for fact in self.facts.values()
            ],
            "calculations": [
                {
                    "calculation_id": calculation.calculation_id,
                    "operation": calculation.operation,
                    "evidence_set": calculation.evidence_set_handle,
                    "complete_population": calculation.complete_population,
                    "values": calculation.rendered_values(),
                }
                for calculation in self.calculations.values()
            ],
            "evidence_sets": [pending.digest() for pending in self.evidence_sets.values()],
            "coverage": self.coverage_meta(),
            "render_templates": list(template_keys),
        }

    def persistence_specs(self) -> list[dict[str, Any]]:
        """Rows for ``ThreadRepository.terminal(evidence_sets=...)``.

        ``authorization_scope_hash`` is stamped by the executor at persist
        time (server-side only); it never rides ``synthesis_view``.
        """
        specs: list[dict[str, Any]] = []
        for pending in self.evidence_sets.values():
            specs.append({
                "id": pending.handle,
                "source_class": pending.source_class,
                "filters": dict(pending.filters),
                "population_count": pending.population_count,
                "evaluated_count": pending.evaluated_count,
                "displayed_count": pending.displayed_count,
                "complete_population": pending.complete_population,
                "high_watermarks": dict(pending.high_watermarks),
                "snapshot_hash": pending.snapshot_hash,
                "supports_expansion": pending.supports_expansion,
                "member_cap": pending.member_cap,
                "calculation": dict(pending.calculation),
                "members": list(pending.members),
            })
        return specs


# -- per-source adapters ---------------------------------------------------


def _value(value_type: ValueType, raw: Any, unit: str | None = None) -> FactValue:
    return FactValue(value_type=value_type, raw=raw, unit=unit)


def facts_from_work_order_row(
    store: EvidenceStore,
    row: Mapping[str, Any],
    *,
    retrieval_id: str,
    as_of: str,
    source_revision: str,
) -> str:
    """One ``work_order_row`` projection (tasks/ai_read.py) → one fact."""
    values: dict[str, FactValue] = {
        "reference": _value("identifier", row.get("reference") or ""),
        "title": _value("text", row.get("title") or ""),
        "board_status": _value("enum", row.get("board_status")),
        "lifecycle_status": _value("enum", row.get("lifecycle_status")),
        "work_order_type": _value("enum", row.get("work_order_type")),
        "priority": _value("enum", row.get("priority")),
        "due_date": _value("date", row.get("due_date")),
        "created_at": _value("datetime", row.get("created_at")),
        "updated_at": _value("datetime", row.get("updated_at")),
        "actual_started_at": _value("datetime", row.get("actual_started_at")),
        "actual_completed_at": _value("datetime", row.get("actual_completed_at")),
    }
    if row.get("machine") is not None:
        values["machine"] = _value("text", row.get("machine"))
    machine_id = row.get("machine_id")
    entity_refs = [f"workorder:{row.get('work_order_id')}"]
    if machine_id is not None:
        entity_refs.append(f"machine:{machine_id}")
    return store.add_fact(
        kind="record_field",
        source_class="work_order",
        source_id=str(row.get("work_order_id")),
        source_revision=source_revision,
        locator={"field": "work_order_row"},
        retrieval_id=retrieval_id,
        as_of=as_of,
        authorization_class="maintenance_authorized",
        values=values,
        entity_refs=entity_refs,
        machine_id=machine_id if isinstance(machine_id, int) else None,
    )


def coverage_fact(
    store: EvidenceStore,
    result: Mapping[str, Any],
    *,
    retrieval_id: str,
    source_class: str,
    as_of: str,
) -> str:
    """A page/corpus result's honest counts (S5 vocabulary) → one fact."""
    return store.add_fact(
        kind="coverage",
        source_class=source_class,
        source_id=retrieval_id,
        source_revision=str(result.get("high_watermark") or ""),
        locator={"field": "coverage"},
        retrieval_id=retrieval_id,
        as_of=as_of,
        authorization_class="maintenance_authorized",
        values={
            "population_count": _value("int", result.get("population_count", 0)),
            "returned_count": _value("int", result.get("returned_count", 0)),
            "complete_population": _value("bool", bool(result.get("complete_population", False))),
        },
    )


def fact_from_manual_citation(
    store: EvidenceStore,
    citation: Mapping[str, Any],
    *,
    retrieval_id: str,
    as_of: str,
    paraphrase_source: bool = True,
) -> str:
    """One controlled-document chunk citation → one controlled fact."""
    return store.add_fact(
        kind="manual_passage",
        source_class="controlled_document",
        source_id=str(citation.get("document_id") or ""),
        source_revision=str(citation.get("revision") or ""),
        locator={
            "chunk": str(citation.get("chunk_id") or ""),
            "section": citation.get("section_path"),
        },
        retrieval_id=retrieval_id,
        as_of=as_of,
        authorization_class="maintenance_authorized",
        values={
            "document": _value("text", citation.get("document") or ""),
            "document_id": _value("identifier", citation.get("document_id") or ""),
            "revision": _value("identifier", citation.get("revision") or ""),
        },
        controlled=True,
    )


def facts_from_inventory_rows(
    store: EvidenceStore,
    rows: Iterable[Mapping[str, Any]],
    *,
    retrieval_id: str,
    as_of: str,
    source_class: str = "source_registry",
) -> list[str]:
    """S8a gateway registry rows → inventory facts (identifier/text values)."""
    fact_ids: list[str] = []
    for row in rows:
        values: dict[str, FactValue] = {}
        for name, raw in row.items():
            if isinstance(raw, bool):
                values[name] = _value("bool", raw)
            elif isinstance(raw, int):
                values[name] = _value("int", raw)
            elif raw is None or isinstance(raw, str):
                values[name] = _value("text", raw)
        fact_ids.append(
            store.add_fact(
                kind="inventory_entry",
                source_class=source_class,
                source_id=str(row.get("document_id") or row.get("attachment_id") or ""),
                source_revision=str(row.get("revision") or ""),
                locator={"field": "registry_row"},
                retrieval_id=retrieval_id,
                as_of=as_of,
                authorization_class="maintenance_authorized",
                values=values,
            )
        )
    return fact_ids


def facts_from_maintenance_record_row(
    store: EvidenceStore,
    row: Mapping[str, Any],
    *,
    retrieval_id: str,
    as_of: str,
    source_revision: str,
) -> str:
    """One maintenance-record projection (tasks/ai_analytics.py) → one fact.

    The record is its machine's plant history — a distinct population from
    work orders (A7) — so it carries its own source class and never masquerades
    as a ``record_field`` work-order fact. Narratives arrive already fenced.
    """
    machine_id = row.get("machine_id")
    values: dict[str, FactValue] = {
        "date": _value("date", row.get("date")),
        "summary": _value("text", row.get("summary") or ""),
        "updated_at": _value("datetime", row.get("updated_at")),
    }
    if row.get("details"):
        values["details"] = _value("text", row.get("details"))
    entity_refs: list[str] = []
    if machine_id is not None:
        entity_refs.append(f"machine:{machine_id}")
    return store.add_fact(
        kind="maintenance_record",
        source_class="maintenance_record",
        source_id=str(row.get("record_id")),
        source_revision=source_revision,
        locator={"field": "maintenance_record_row"},
        retrieval_id=retrieval_id,
        as_of=as_of,
        authorization_class="maintenance_authorized",
        values=values,
        entity_refs=entity_refs,
        machine_id=machine_id if isinstance(machine_id, int) else None,
    )


#: How a group/bucket row's cells become typed values. This map IS the cell
#: vocabulary — a key outside it never enters the store, so a stray column
#: cannot smuggle text into a rendered table.
_GROUP_ROW_VALUE_TYPES: dict[str, ValueType] = {
    "key": "identifier",
    "label": "text",
    "bucket": "date",
    "group_count": "int",
    "event_count": "int",
    "interval_count": "int",
    "min_days": "decimal",
    "median_days": "decimal",
    "mean_days": "decimal",
    "max_days": "decimal",
    # S9 comparison step rows:
    "status": "enum",
}


def facts_from_group_rows(
    store: EvidenceStore,
    rows: Iterable[Mapping[str, Any]],
    *,
    retrieval_id: str,
    as_of: str,
    source_class: str,
    source_revision: str,
    grouping: str,
) -> list[str]:
    """Aggregate/timeline group rows → one fact per row (S7).

    Every cell a breakdown table will ever show is server-inserted here
    first, which is what lets the C05 value-closure hold for an iterated
    table by construction. ``grouping='machine'`` rows carry the machine
    entity so chips and reauthorization see them.
    """
    fact_ids: list[str] = []
    for index, row in enumerate(rows):
        values: dict[str, FactValue] = {}
        for name, value_type in _GROUP_ROW_VALUE_TYPES.items():
            if name in row and row[name] is not None:
                values[name] = _value(value_type, row[name])
        machine_id = row.get("key") if grouping == "machine" else None
        entity_refs: list[str] = []
        if isinstance(machine_id, int):
            entity_refs.append(f"machine:{machine_id}")
        else:
            machine_id = None
        fact_ids.append(
            store.add_fact(
                kind="group_row",
                source_class=source_class,
                source_id=str(row.get("key", row.get("bucket", index))),
                source_revision=source_revision,
                locator={"field": "group_row", "grouping": grouping, "index": index},
                retrieval_id=retrieval_id,
                as_of=as_of,
                authorization_class="maintenance_authorized",
                values=values,
                entity_refs=entity_refs,
                machine_id=machine_id,
            )
        )
    return fact_ids


def fact_from_dataset_profile(
    store: EvidenceStore,
    profile: Mapping[str, Any],
    *,
    retrieval_id: str,
    as_of: str,
) -> str:
    """A dataset profile (§8.3 op 1) → one fact of honest counts."""
    values: dict[str, FactValue] = {
        "population_count": _value("int", profile.get("population_count", 0)),
        "null_date_count": _value("int", profile.get("null_date_count", 0)),
        "unassigned_machine_count": _value("int", profile.get("unassigned_machine_count", 0)),
        "distinct_machine_count": _value("int", profile.get("distinct_machine_count", 0)),
        "date_field": _value("enum", profile.get("date_field")),
        "timezone": _value("text", profile.get("timezone")),
        "complete_population": _value("bool", bool(profile.get("complete_population", False))),
    }
    if profile.get("date_min"):
        values["date_min"] = _value("datetime", profile.get("date_min"))
    if profile.get("date_max"):
        values["date_max"] = _value("datetime", profile.get("date_max"))
    return store.add_fact(
        kind="dataset_profile",
        source_class=str(profile.get("population_type") or "work_order"),
        source_id=retrieval_id,
        source_revision=str(profile.get("high_watermark") or ""),
        locator={"field": "dataset_profile"},
        retrieval_id=retrieval_id,
        as_of=as_of,
        authorization_class="maintenance_authorized",
        values=values,
    )


def fact_from_procedure_application(
    store: EvidenceStore,
    application: Mapping[str, Any],
    *,
    retrieval_id: str,
    as_of: str,
) -> str:
    """The applied procedure revision (tasks reader projection) → one fact.

    Governed content, byte-anchored: ``controlled=True`` with the revision
    ``content_hash`` as the source revision — S9's stage-1 evidence.
    """
    values: dict[str, FactValue] = {
        "procedure_code": _value("identifier", application.get("procedure_code") or ""),
        "procedure_name": _value("text", application.get("procedure_name") or ""),
        "revision": _value("int", application.get("revision")),
        "content_hash": _value("identifier", application.get("content_hash") or ""),
        "drift_status": _value("enum", application.get("drift_status")),
        "applied_at": _value("datetime", application.get("applied_at")),
        "step_count": _value("int", application.get("step_count", 0)),
    }
    return store.add_fact(
        kind="record_field",
        source_class="procedure_application",
        source_id=str(application.get("application_id")),
        source_revision=str(application.get("content_hash") or ""),
        locator={"field": "procedure_application"},
        retrieval_id=retrieval_id,
        as_of=as_of,
        authorization_class="maintenance_authorized",
        values=values,
        controlled=True,
    )


def fact_from_applicability_claim(
    store: EvidenceStore,
    claim: Any,
    *,
    retrieval_id: str,
    as_of: str,
) -> str:
    """One VERIFIED ``ControlledDocumentApplicability`` row → one fact (S8b).

    The C07-extension basis: a template that requires verified
    applicability must cite one of these, and the fact carries the machine
    entity so the check can match it against the claim's entities. The
    revision pin is the copied content hash — byte-anchored like the row.
    """
    values: dict[str, FactValue] = {
        "applicability_kind": _value("enum", getattr(claim, "kind", "")),
        "applicability_state": _value("enum", getattr(claim, "state", "")),
        "document_id": _value(
            "identifier", getattr(getattr(claim, "document", None), "document_id", "")
        ),
        "revision": _value("identifier", getattr(getattr(claim, "document", None), "revision", "")),
        "verified": _value("bool", str(getattr(claim, "state", "")) == "verified"),
    }
    if getattr(claim, "effective_from", None):
        values["effective_from"] = _value("date", claim.effective_from.isoformat())
    if getattr(claim, "effective_to", None):
        values["effective_to"] = _value("date", claim.effective_to.isoformat())
    machine_id = int(getattr(claim, "target_machine_id", 0) or 0)
    entity_refs: list[str] = []
    if machine_id > 0:
        entity_refs.append(f"machine:{machine_id}")
    if getattr(claim, "target_model", ""):
        values["target_model"] = _value("text", claim.target_model)
    return store.add_fact(
        kind="applicability",
        source_class="applicability",
        source_id=str(getattr(claim, "pk", "")),
        source_revision=str(getattr(claim, "document_content_sha256", "")),
        locator={"field": "applicability_claim"},
        retrieval_id=retrieval_id,
        as_of=as_of,
        authorization_class="maintenance_authorized",
        values=values,
        entity_refs=entity_refs,
        machine_id=machine_id if machine_id > 0 else None,
        controlled=True,
    )


__all__ = [
    "EVIDENCE_SET_MEMBER_CAP",
    "CalculationOutput",
    "EvidenceStore",
    "FactValue",
    "PendingEvidenceSet",
    "TypedFact",
    "coverage_fact",
    "fact_from_applicability_claim",
    "fact_from_dataset_profile",
    "fact_from_manual_citation",
    "fact_from_procedure_application",
    "facts_from_group_rows",
    "facts_from_inventory_rows",
    "facts_from_maintenance_record_row",
    "facts_from_work_order_row",
    "mint_evidence_set_handle",
]
