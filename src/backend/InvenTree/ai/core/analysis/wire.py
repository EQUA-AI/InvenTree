"""Analysis-scope wire-contract models (S1 residue, WP1).

The scope endpoints (`GET/PUT /threads/{id}/scope`) and the thread
list/detail projections build their payloads in
``aichat.services.threads`` (``_scope_payload`` / ``scope_summary``) and
``ai.core.analysis.scope`` (``scope_to_payload``). These pydantic models
mirror those shapes so ``manage.py generate_wire_contract`` can emit the
TypeScript interfaces, exactly as ``ai.core.voice.wire`` does for the
voice rail. Round-trip tests (``test_scope_wire.py``,
``aichat/tests/test_thread_analysis_scope.py``) pin the mirrors to the
live payloads — hand drift fails CI via the byte-exact ``--check``.

These models validate nothing on the serving path; the serving authority
stays in ``scope.py``/``threads.py``.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class AnalysisScopeDateWindow(BaseModel):
    """Half-open ``[from, to)`` ISO-date window; both ends optional."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: str | None = Field(default=None, alias="from")
    to: str | None = None


class AnalysisScopePayload(BaseModel):
    """The stored/wire scope shape (mirror of ``scope_to_payload``)."""

    model_config = ConfigDict(extra="forbid")

    schema_version: int
    mode: str
    machine_ids: list[int]
    date_window: AnalysisScopeDateWindow
    source_classes: list[str]
    display_label: str


class AnalysisScopeUpdate(BaseModel):
    """A client scope request (input to ``normalize_scope_request``)."""

    model_config = ConfigDict(extra="forbid")

    mode: str
    machine_ids: list[int] | None = None
    date_window: AnalysisScopeDateWindow | None = None
    source_classes: list[str] | None = None
    display_label: str | None = None


class ActiveScopeSummary(BaseModel):
    """Compact scope row on thread list/detail (mirror of ``scope_summary``)."""

    model_config = ConfigDict(extra="forbid")

    mode: str
    version: int
    display_label: str


class ThreadScopePayload(BaseModel):
    """Full scope payload from ``GET/PUT /threads/{id}/scope``."""

    model_config = ConfigDict(extra="forbid")

    thread_id: str
    scope: AnalysisScopePayload
    version: int
    hash: str
    display_label: str
    editable: bool


class ThreadScopeUpdateRequest(BaseModel):
    """The ``PUT /threads/{id}/scope`` body (mirror of the route model)."""

    model_config = ConfigDict(extra="forbid")

    expected_version: int
    scope: AnalysisScopeUpdate


#: Machine-readable ``detail`` values the scope rail can return (the
#: ``SERVER_VOICE_ERROR_CODES`` precedent). Pinned to the ``app.py``
#: literals by ``test_scope_wire.py``.
SCOPE_ERROR_CODES: tuple[str, ...] = (
    "scope_version_conflict",
    "scope_update_rejected",
)


# --- Evidence analysis v2 (S10/S11) -----------------------------------------
#
# The consolidated ``evidence_analysis`` attachment rides three envelopes
# with ONE object shape: legacy SSE ``STATE_DELTA {kind: "evidence_analysis"}``,
# the AG-UI ``aimms.evidenceAnalysis`` CUSTOM channel, and the persisted
# message ``metadata["evidence_analysis"]`` served by the thread reload
# projection. These mirrors exist so ``generate_wire_contract`` can emit the
# TypeScript shapes; the serving authority stays in the analysis executor.

#: Content-free progress stages emitted before validation (the only
#: pre-validation stream besides tool events). Closed enum by design: the
#: client maps stage -> localized string, server free text never paints.
ANALYSIS_PROGRESS_STAGES: tuple[str, ...] = (
    "confirming_scope",
    "reviewing_records",
    "validating_evidence",
)

#: The six distinct no-data states of §8.8. The client renders ONLY a
#: server-declared reason — an empty result set is never converted into
#: "no records exist" client-side.
ANALYSIS_NO_DATA_REASONS: tuple[str, ...] = (
    "complete_population_no_matches",
    "outside_active_selection",
    "unauthorized_or_unavailable",
    "retrieval_failure",
    "unresolved_applicability",
    "incomplete_coverage",
)


class RetrievalCoveragePayload(BaseModel):
    """The coverage block of one evidence-analysis answer (§7.4 vocabulary)."""

    model_config = ConfigDict(extra="forbid")

    population_count: int
    returned_count: int
    complete_population: bool
    display_truncated: bool
    date_field: str | None
    timezone: str | None
    filters: list[str]
    as_of: str
    snapshot_label: str | None
    excluded_null_date_count: int | None
    incomplete_reason: str | None


class CitationLocator(BaseModel):
    """Display locator for one citation row."""

    model_config = ConfigDict(extra="forbid")

    page: int | None
    section: str | None
    field: str | None


class CitationManifestEntry(BaseModel):
    """One ordinal in the server-minted citation manifest.

    Ordinals match the literal ``[n]`` markers the renderer inserted into
    ``detailed_response`` — the client never derives them from array order.
    """

    model_config = ConfigDict(extra="forbid")

    ordinal: int
    source_type: str
    source_id: str | None
    source_title: str | None
    source_revision: str | None
    source_class: str | None
    controlled: bool
    as_of: str
    available: bool
    locator: CitationLocator | None
    applicability: str | None
    evidence_set_id: str | None
    calculation: str | None


class ClaimPayload(BaseModel):
    """One wire claim; cites by citation ORDINAL, never by internal ref ids."""

    model_config = ConfigDict(extra="forbid")

    claim_id: str
    claim_role: str
    claim_type: str
    evidence_classification: str
    citation_ordinals: list[int]
    entity_refs: list[str]


class AnalysisScopeStamp(BaseModel):
    """Turn-time active-scope snapshot carried INSIDE the live payload.

    Required so live copy/export reads the scope as bound at turn time,
    never the mutable thread scope (Q83 fidelity).
    """

    model_config = ConfigDict(extra="forbid")

    display_label: str
    version: int


class AnalysisIncompleteReasonPayload(BaseModel):
    """Typed reason one facet did not complete (partial-state contract)."""

    model_config = ConfigDict(extra="forbid")

    code: str
    facet: str


class EvidenceSetMember(BaseModel):
    """One member row from the evidence-set expansion endpoint.

    ``member_index`` (not ``ordinal``) to avoid colliding with citation
    ordinals; an unavailable member carries nothing beyond the index —
    revocation and deletion are indistinguishable by design.
    """

    model_config = ConfigDict(extra="forbid")

    member_index: int
    source_class: str
    source_object_id: str | None
    label: str | None
    available: bool


class EvidenceSetPage(BaseModel):
    """One page of evidence-set members.

    ``next_cursor`` is an opaque, signed, expiring token bound to the
    actor + thread + set; ``null`` on the final page.
    """

    model_config = ConfigDict(extra="forbid")

    members: list[EvidenceSetMember]
    population_count: int
    complete: bool
    next_cursor: str | None


__all__ = [
    "ANALYSIS_NO_DATA_REASONS",
    "ANALYSIS_PROGRESS_STAGES",
    "SCOPE_ERROR_CODES",
    "ActiveScopeSummary",
    "AnalysisIncompleteReasonPayload",
    "AnalysisScopeDateWindow",
    "AnalysisScopePayload",
    "AnalysisScopeStamp",
    "AnalysisScopeUpdate",
    "CitationLocator",
    "CitationManifestEntry",
    "ClaimPayload",
    "EvidenceSetMember",
    "EvidenceSetPage",
    "RetrievalCoveragePayload",
    "ThreadScopePayload",
    "ThreadScopeUpdateRequest",
]
